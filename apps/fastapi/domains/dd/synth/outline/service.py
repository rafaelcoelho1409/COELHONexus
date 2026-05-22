from __future__ import annotations

from collections import defaultdict, deque
from typing import Optional

from .constants import (
    _BANNED_HEADINGS_LC,
    _BANNED_LIST_HUMAN,
    _MAX_STAGE_DEPTH,
)
from .types import ChapterOutline, OutlineDAG, OutlineSection


# =============================================================================
# DAG primitives (pure)
# =============================================================================
def build_edges(sections: list[OutlineSection]) -> list[tuple[str, str]]:
    """Materialize edges from each section's `prerequisites` field.

    Edge (p, s) means "p is a prerequisite of s" — reader absorbs p
    BEFORE s. Silently skips prereqs that reference unknown section_ids
    (validate_outline_structure flags those separately so callers can
    decide whether to retry vs auto-prune).
    """
    known = {s.section_id for s in sections}
    edges: list[tuple[str, str]] = []
    for s in sections:
        for prereq in s.prerequisites:
            if prereq in known and prereq != s.section_id:
                edges.append((prereq, s.section_id))
    return edges


def _find_cycle(
    nodes: list[str], edges: list[tuple[str, str]],
) -> Optional[list[str]]:
    """Return ONE cycle (list of nodes in cycle order) if any, else None.

    Uses iterative DFS with a recursion stack to support large graphs
    without hitting Python's recursion limit (sections capped at 40 so
    overflow can't happen in practice, but cheap to be safe).
    """
    adj: dict[str, list[str]] = defaultdict(list)
    for u, v in edges:
        adj[u].append(v)

    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {n: WHITE for n in nodes}
    parent: dict[str, Optional[str]] = {n: None for n in nodes}

    for start in nodes:
        if color[start] != WHITE:
            continue
        stack: list[tuple[str, iter]] = [(start, iter(adj[start]))]
        color[start] = GRAY
        while stack:
            u, it = stack[-1]
            advanced = False
            for v in it:
                if color[v] == WHITE:
                    color[v] = GRAY
                    parent[v] = u
                    stack.append((v, iter(adj[v])))
                    advanced = True
                    break
                if color[v] == GRAY:
                    # Found a back edge u → v: cycle is v ... u → v.
                    cycle = [v]
                    cur = u
                    while cur is not None and cur != v:
                        cycle.append(cur)
                        cur = parent[cur]
                    cycle.append(v)
                    cycle.reverse()
                    return cycle
            if not advanced:
                color[u] = BLACK
                stack.pop()
    return None


def break_cycles_fas(
    nodes: list[str], edges: list[tuple[str, str]],
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Remove edges until acyclic. Returns (kept_edges, removed_edges).

    Greedy strategy (mirrors SurveyGen-I's "remove one edge per cycle"):
    on each detected cycle, remove the LAST edge in the cycle path.
    Last-edge removal heuristic = preserve the longest dependency
    prefix; rarely matters since LLM-generated cycles are usually small.
    """
    kept = list(edges)
    removed: list[tuple[str, str]] = []
    while True:
        cycle = _find_cycle(nodes, kept)
        if cycle is None:
            break
        if len(cycle) < 2:
            break
        last_edge = (cycle[-2], cycle[-1])
        try:
            kept.remove(last_edge)
            removed.append(last_edge)
        except ValueError:
            # Should not happen — the edge came from the cycle path
            # which was built from kept edges. Defensive break.
            break
    return kept, removed


def compute_stage_indices(
    nodes: list[str], edges: list[tuple[str, str]],
) -> dict[str, int]:
    """Longest-path topological labeling.

    `τ(s) = 0 if In(s) = ∅ else max(τ(p)+1 for p in In(s))` — matches
    SurveyGen-I §3.1 formula. Returns {node: stage_index}. Assumes
    `edges` is acyclic (caller must run `break_cycles_fas` first).
    """
    in_edges: dict[str, list[str]] = {n: [] for n in nodes}
    out_edges: dict[str, list[str]] = {n: [] for n in nodes}
    for u, v in edges:
        out_edges[u].append(v)
        in_edges[v].append(u)
    indeg = {n: len(in_edges[n]) for n in nodes}
    stage: dict[str, int] = {n: 0 for n in nodes}
    queue: deque[str] = deque(n for n in nodes if indeg[n] == 0)
    while queue:
        u = queue.popleft()
        for v in out_edges[u]:
            stage[v] = max(stage[v], stage[u] + 1)
            indeg[v] -= 1
            if indeg[v] == 0:
                queue.append(v)
    return stage


def derive_dag(sections: list[OutlineSection]) -> OutlineDAG:
    """One-shot DAG derivation: edges + cycle-break + stage_index.

    The complete deterministic pipeline that follows a validated
    ChapterOutline. Idempotent.
    """
    nodes = [s.section_id for s in sections]
    raw_edges = build_edges(sections)
    edges, removed = break_cycles_fas(nodes, raw_edges)
    stage_index = compute_stage_indices(nodes, edges)
    stages: dict[int, list[str]] = defaultdict(list)
    for n, i in stage_index.items():
        stages[i].append(n)
    # Stable order within stage = LLM-emitted section order.
    order = {n: i for i, n in enumerate(nodes)}
    for i in stages:
        stages[i].sort(key=lambda n: order[n])
    return OutlineDAG(
        edges=edges,
        stage_index=stage_index,
        stages=dict(stages),
        max_stage=max(stage_index.values()) if stage_index else 0,
        removed_edges=removed,
    )


# =============================================================================
# Structural validators (post-Pydantic, fail-soft for repair loop)
# =============================================================================
def validate_outline_structure(
    outline: ChapterOutline, dag: OutlineDAG,
) -> tuple[bool, list[str]]:
    """Return (ok, list_of_issues). Issues are natural-language strings
    suitable for feeding back to the LLM as repair instructions.

    Pydantic already enforces section-level rules (id format, heading
    length, description length, prereq count). This function checks
    CROSS-section invariants:

      - section_ids are globally unique
      - case-folded headings are unique
      - no banned headings appear
      - every prereq references an existing section_id
      - DAG depth ≤ _MAX_STAGE_DEPTH (rejects linear-only outlines)
      - first-stage sections exist (would only fail if every section
        has prereqs — implies a cycle that FAS broke into a forest)
    """
    issues: list[str] = []
    ids = [s.section_id for s in outline.sections]
    headings_lc = [s.heading.casefold() for s in outline.sections]

    if len(set(ids)) != len(ids):
        seen: set[str] = set()
        dupes: list[str] = []
        for sid in ids:
            if sid in seen:
                dupes.append(sid)
            seen.add(sid)
        issues.append(
            f"Duplicate section_ids: {sorted(set(dupes))} — section ids "
            f"must be unique."
        )

    if len(set(headings_lc)) != len(headings_lc):
        issues.append(
            "Duplicate section headings (case-insensitive) — every "
            "section must have a distinct heading."
        )

    bad_headings = [
        s.heading for s in outline.sections
        if s.heading.casefold() in _BANNED_HEADINGS_LC
    ]
    if bad_headings:
        issues.append(
            f"Banned headings present (content-type names, not topics): "
            f"{bad_headings}. Use topic-specific headings instead."
        )

    known_ids = set(ids)
    for s in outline.sections:
        for prereq in s.prerequisites:
            if prereq not in known_ids:
                issues.append(
                    f"Section {s.section_id} lists prerequisite "
                    f"{prereq!r} which does not exist in the outline."
                )

    if dag.max_stage > _MAX_STAGE_DEPTH:
        issues.append(
            f"DAG depth {dag.max_stage} exceeds maximum "
            f"{_MAX_STAGE_DEPTH}. Outline is too linear — flatten by "
            f"removing transitive prerequisites (only direct deps; "
            f"don't chain s1→s2→s3→s4 when s3 is the only true prereq "
            f"of s4)."
        )

    if dag.removed_edges:
        issues.append(
            f"Outline had cycles that were auto-broken by removing "
            f"edges: {dag.removed_edges}. Re-emit without circular "
            f"prerequisites — every prereq must point to an EARLIER "
            f"section in the reader's path, not a later one."
        )

    if not any(i == 0 for i in dag.stage_index.values()):
        issues.append(
            "No section has stage_index=0 — every section claims a "
            "prerequisite. At least one section MUST have empty "
            "prerequisites (the chapter's entry point)."
        )

    return (len(issues) == 0, issues)


# =============================================================================
# Prompt templates
# =============================================================================
def build_outline_prompt(
    *,
    framework: str,
    chapter_id: str,
    chapter_title: str,
    chapter_description: str,
    n_vault_hashes: int,
    sources_concat_md: str,
    target_sections_hint: int = 8,
) -> str:
    """Build the OUTLINE_SDP prompt.

    `target_sections_hint` is a soft target (default 8); the schema's
    hard min/max are 4/40. SurveyGen-I §3.1 doesn't fix a target but
    their reported chapters average 6-12 subsections.
    """
    return (
        f"You are the Chapter Outliner — `outline_sdp`, step 3 of the "
        f"Docs Distiller synth pipeline. Per SurveyGen-I PlanEvo "
        f"(arXiv 2508.14317 §3.1), your single job is to PRE-DECOMPOSE "
        f"the chapter into 4-40 sections (soft target ~{target_sections_hint}) "
        f"with EXPLICIT inter-section dependencies. You write NO prose "
        f"bodies and place NO code — that happens downstream in "
        f"`sawc_write` after `digest_construct` routes the source "
        f"material to your sections.\n\n"

        f"FRAMEWORK: {framework}\n"
        f"CHAPTER: {chapter_id} — {chapter_title}\n"
        f"CHAPTER GOAL: {chapter_description}\n"
        f"VAULT SIZE (estimate): {n_vault_hashes} code blocks across "
        f"the source material below.\n\n"

        f"== SOURCE MATERIAL (already normalized; vault sentinels like "
        f"`<code-ref hash=\"...\"/>` may appear — IGNORE them, "
        f"digest_construct handles routing) ==\n"
        f"{sources_concat_md}\n"
        f"== END SOURCE MATERIAL ==\n\n"

        f"OUTPUT — strict JSON matching this schema:\n"
        f"{{\n"
        f'  "sections": [\n'
        f'    {{\n'
        f'      "section_id":    "s1",   /* lowercase s + integer; s1, s2, ... */\n'
        f'      "heading":       "2-8 words, topic-specific, no leading #",\n'
        f'      "description":   "1-line topic spec, 20-400 chars",\n'
        f'      "prerequisites": ["s_id", ...],  /* 0-3 ids of EARLIER sections */\n'
        f'      "needs_code":    true            /* false for design narratives */\n'
        f'    }},\n'
        f'    ... 4-40 entries ...\n'
        f'  ],\n'
        f'  "challenges": [\n'
        f'    "5-10 active-recall questions; mix conceptual + applied; one string per item"\n'
        f'  ],\n'
        f'  "flashcards": [\n'
        f'    {{"q": "...", "a": "..."}}, ... 4-15 entries ...\n'
        f'  ]\n'
        f"}}\n\n"

        f"== HARD RULES ==\n"
        f"1. section_id format: 's' + integer, e.g. 's1', 's2', ..., 's40'. "
        f"Unique within the chapter. Once assigned, an id is referenced by "
        f"downstream nodes — do NOT renumber on subsequent rewrites.\n"
        f"2. heading: 2-8 words, topic-y/code-y, NO leading '#'. BANNED "
        f"(case-insensitive — these are content-types, not topics): "
        f"{_BANNED_LIST_HUMAN}.\n"
        f"3. description: 20-400 chars, ONE specific topic. Used by "
        f"`digest_construct` to route source material — vague descriptions "
        f"cause mis-routing. Examples of good: 'how to wire DI overrides "
        f"for tests'; 'the streaming response shape for tool calls'. "
        f"Examples of bad: 'various features'; 'examples and usage'.\n"
        f"4. prerequisites: list 0-3 section_ids the reader must absorb "
        f"BEFORE this one. STRUCTURAL deps only (e.g. 's3 uses the "
        f"runnable lifecycle defined in s1' → s1 ∈ s3.prerequisites), "
        f"not merely thematic. The FIRST logical section (lowest stage) "
        f"MUST have an empty list. Subsequent sections MAY have 0 "
        f"prereqs if they're independent of prior sections.\n"
        f"5. Prerequisites form a DAG: NO cycles, NO self-references, NO "
        f"forward references (only point BACKWARD to EARLIER sections in "
        f"the reader's path). Aim for max-depth 3-4 stages — deeper DAGs "
        f"linearize the chapter (kills parallel writing downstream).\n"
        f"6. needs_code: true if the section will reference code patterns / "
        f"APIs / configs / runnable examples. false for pure design "
        f"narrative, ecosystem context, or conceptual material.\n\n"

        f"== DECOMPOSITION GUIDANCE ==\n"
        f"- Each section should cover ~5-15 vault hashes (estimate from "
        f"natural topical clusters in the source: fences under a common "
        f"heading, one API surface, one config concern, one error mode).\n"
        f"- Prefer DEEPER-AND-NARROWER (split a too-broad section into 2 "
        f"with a prereq edge between them) over WIDER-AND-FLATTER (one "
        f"section trying to cover 25 fences).\n"
        f"- Encourage parallelism by keeping prereq chains short — many "
        f"shallow branches > one long sequential spine.\n"
        f"- Order sections by reading flow: stage 0 (no prereqs) → "
        f"stage 1 → ... Each prereq chain represents one teaching arc.\n"
        f"- DO NOT duplicate topics across sections — `digest_construct` "
        f"routes each source artifact to exactly ONE section; overlapping "
        f"headings force an arbitrary pick.\n\n"

        f"== CHALLENGES + FLASHCARDS ==\n"
        f"- challenges: 5-10 active-recall questions covering the WHOLE "
        f"chapter (not any single section). Mix conceptual ('Why does X "
        f"block on Y?') and applied ('Write a function that uses Z').\n"
        f"- flashcards: 4-15 Anki Q/A pairs. Each pair stand-alone (no "
        f"references to 'see section X'). q: question; a: answer with "
        f"a concrete example where applicable.\n\n"

        f"Respond ONLY with valid JSON matching the schema above. NO "
        f"prose commentary, NO markdown wrapping, NO explanation — the "
        f"JSON is parsed directly by the next graph node."
    )


def build_usc_vote_prompt(
    *,
    candidates_summary: list[dict],
    chapter_id: str,
    chapter_title: str,
) -> str:
    """USC picker prompt — Universal Self-Consistency rubric over N
    candidate outlines.

    `candidates_summary` is a list of dicts with structural metadata
    (n_sections, max_stage, n_violations, heading_list, etc.) computed
    deterministically. The LLM picker reads the summaries — NOT the
    full outline JSON — to keep its context small and its rubric
    focused on structure (per Brown & Cobbe 2025: USC pickers degrade
    when fed too much candidate body content).

    Returns a prompt that asks for `{"chosen_index": int}`.
    """
    lines: list[str] = []
    for i, c in enumerate(candidates_summary):
        violations = c.get("violations") or []
        viol_str = (
            f" violations=({len(violations)}: " + "; ".join(violations[:3]) + ")"
            if violations else " violations=(none)"
        )
        headings = c.get("headings") or []
        headings_short = ", ".join(f"{h!r}" for h in headings[:6])
        if len(headings) > 6:
            headings_short += f", ... +{len(headings) - 6} more"
        lines.append(
            f"[{i}] n_sections={c.get('n_sections')}, "
            f"max_stage={c.get('max_stage')}, "
            f"n_stages={c.get('n_stages')}, "
            f"avg_prereqs={c.get('avg_prereqs', 0.0):.2f}, "
            f"n_removed_edges={c.get('n_removed_edges', 0)}, "
            f"n_challenges={c.get('n_challenges')}, "
            f"n_flashcards={c.get('n_flashcards')}, "
            f"avg_desc_chars={c.get('avg_desc_chars', 0):.0f}"
            f"{viol_str}\n"
            f"     headings: {headings_short}"
        )
    candidates_block = "\n".join(lines)
    return (
        f"You are picking the SINGLE BEST outline for chapter "
        f"{chapter_id} ({chapter_title!r}) from {len(candidates_summary)} "
        f"candidates. Each candidate's structural summary is below "
        f"(headings + DAG shape + violation count). Pick by this "
        f"rubric, IN ORDER:\n\n"

        f"1. ZERO structural violations (banned headings, duplicate ids, "
        f"missing prereqs, deep DAG, cycles). A candidate with "
        f"violations LOSES to any candidate without — even if its "
        f"headings are better.\n"
        f"2. Section count within the soft sweet spot (6-12 for typical "
        f"chapters; 12-20 for hash-dense chapters). Outliers (≤5 or "
        f"≥30) signal under/over-decomposition.\n"
        f"3. DAG shape: prefer 2-3 stages with multiple branches over "
        f"1 stage (no deps at all — wasted scheduling info) or 4+ "
        f"stages (over-linearized).\n"
        f"4. Heading specificity: prefer concrete code-y nouns "
        f"('Async Client', 'Tool Calling') over abstract/category-y "
        f"phrases ('Core Concepts', 'Common Patterns').\n"
        f"5. Description quality (avg_desc_chars 80-250 is healthy; "
        f"<60 = too vague, >300 = run-on).\n\n"

        f"Candidates:\n{candidates_block}\n\n"
        f"Respond ONLY with valid JSON: {{\"chosen_index\": <int>}} "
        f"where the integer is 0..{len(candidates_summary) - 1}. "
        f"No prose, no explanation."
    )


def build_repair_prompt(
    *,
    framework: str,
    chapter_id: str,
    chapter_title: str,
    chapter_description: str,
    current_outline_json: str,
    issues: list[str],
    sources_concat_md: str,
) -> str:
    """Repair prompt — given a structurally-invalid outline + issue list,
    ask the LLM to emit a fixed version with the SAME JSON schema.

    Mirrors the deprecated `validate_outline → repair` flow but with
    machine-readable issue strings (vs prose feedback). The LLM keeps
    section_ids stable where possible so downstream nodes can
    cross-reference between iterations.
    """
    issues_block = "\n".join(f"- {x}" for x in issues)
    return (
        f"Fix structural issues in this chapter outline. Keep the SAME "
        f"JSON schema (sections + challenges + flashcards). Preserve "
        f"section_ids and headings that are already good; only change "
        f"what's needed to clear the issues below. NEVER renumber "
        f"section_ids unless you're adding a new section — downstream "
        f"nodes reference them by id.\n\n"

        f"FRAMEWORK: {framework}\n"
        f"CHAPTER: {chapter_id} — {chapter_title}\n"
        f"GOAL: {chapter_description}\n\n"

        f"CURRENT OUTLINE:\n{current_outline_json}\n\n"

        f"ISSUES TO FIX:\n{issues_block}\n\n"

        f"== SOURCE MATERIAL (for context only) ==\n"
        f"{sources_concat_md}\n"
        f"== END SOURCE MATERIAL ==\n\n"

        f"Respond ONLY with valid JSON matching the original schema. "
        f"NO commentary, NO markdown wrapping."
    )


# =============================================================================
# Helpers used by the LangGraph node
# =============================================================================
def summarize_candidate(
    outline: ChapterOutline, dag: OutlineDAG, issues: list[str],
) -> dict:
    """Structural summary for the USC picker. Keeps the picker's
    context small (~200 tokens per candidate) and biases the decision
    toward STRUCTURE — not content (the picker can't reasonably evaluate
    400-char descriptions × 40 sections in a small prompt window)."""
    headings = [s.heading for s in outline.sections]
    desc_chars = [len(s.description) for s in outline.sections]
    n_prereqs = [len(s.prerequisites) for s in outline.sections]
    return {
        "n_sections":      len(outline.sections),
        "max_stage":       dag.max_stage,
        "n_stages":        len(dag.stages),
        "avg_prereqs":     (sum(n_prereqs) / len(n_prereqs)) if n_prereqs else 0.0,
        "n_removed_edges": len(dag.removed_edges),
        "n_challenges":    len(outline.challenges),
        "n_flashcards":    len(outline.flashcards),
        "avg_desc_chars":  (sum(desc_chars) / len(desc_chars)) if desc_chars else 0.0,
        "headings":        headings,
        "violations":      issues,
    }


def count_vault_sentinels(md_text: str) -> int:
    """Cheap estimate of vault size for prompt context. Looks for
    `<code-ref hash="..."/>` tags that `corpus_normalize` + ingestion's
    `vault_sentinelize` leave behind."""
    return md_text.count("<code-ref hash=")
