"""outline_sdp — pure helpers: DAG primitives, structural validation, prompt
builders, JSON/Pydantic parsing, source concatenation, and manifest hashing.
No I/O, no LLM calls, no logging — see service.py for the orchestration
(LLM sampling, USC voting, semantic dedup, MinIO persistence) that calls
these."""
from __future__ import annotations
from . import params, patterns, schemas, versions

import json
import re
from collections import defaultdict, deque
from difflib import SequenceMatcher
from hashlib import sha256
from typing import Optional

from pydantic import ValidationError


# ---------------------------------------------------------------------------
# DAG primitives (pure)
# ---------------------------------------------------------------------------

def build_edges(sections: list[schemas.OutlineSection]) -> list[tuple[str, str]]:
    """Edge (p, s) means p is a prerequisite of s. Unknown prereqs are
    silently skipped (validate_outline_structure flags them separately)."""
    known = {s.section_id for s in sections}
    edges: list[tuple[str, str]] = []
    for s in sections:
        for prereq in s.prerequisites:
            if prereq in known and prereq != s.section_id:
                edges.append((prereq, s.section_id))
    return edges


def find_cycle(
    nodes: list[str], edges: list[tuple[str, str]],
) -> Optional[list[str]]:
    """One cycle (nodes in order) or None. Iterative DFS — sections cap at 40."""
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
    """Remove edges until acyclic. Greedy: drop the LAST edge per cycle
    (preserves longest dep prefix). SurveyGen-I style."""
    kept = list(edges)
    removed: list[tuple[str, str]] = []
    while True:
        cycle = find_cycle(nodes, kept)
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
    """Longest-path topological labeling (SurveyGen-I §3.1).
    `τ(s) = 0 if In(s) = ∅ else max(τ(p)+1 ...)`. Edges must be acyclic."""
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


def derive_dag(sections: list[schemas.OutlineSection]) -> schemas.OutlineDAG:
    """One-shot DAG: edges → break cycles → stage_index. Idempotent."""
    nodes = [s.section_id for s in sections]
    raw_edges = build_edges(sections)
    edges, removed = break_cycles_fas(nodes, raw_edges)
    stage_index = compute_stage_indices(nodes, edges)
    stages: dict[int, list[str]] = defaultdict(list)
    for n, i in stage_index.items():
        stages[i].append(n)
    # Stable within-stage order = LLM-emitted section order.
    order = {n: i for i, n in enumerate(nodes)}
    for i in stages:
        stages[i].sort(key = lambda n: order[n])
    return schemas.OutlineDAG(
        edges = edges,
        stage_index = stage_index,
        stages = dict(stages),
        max_stage = max(stage_index.values()) if stage_index else 0,
        removed_edges = removed,
    )


def validate_outline_structure(
    outline: schemas.ChapterOutline,
    dag: schemas.OutlineDAG,
    *,
    n_sources: Optional[int] = None,
) -> tuple[bool, list[str]]:
    """(ok, issues): cross-section checks Pydantic can't handle (unique ids/headings, banned headings, valid prereqs, DAG depth cap, fuzzy-dup H2, adaptive section count)."""
    issues: list[str] = []
    ids = [s.section_id for s in outline.sections]
    headings_lc = [s.heading.casefold() for s in outline.sections]

    # Adaptive cap from source pool size; falls back to SECTIONS_MAX
    # ceiling when n_sources is None.
    if n_sources is not None:
        adaptive_cap = params.max_h2_for_n_sources(n_sources)
        n_h2 = len(outline.sections)
        if n_h2 > adaptive_cap:
            issues.append(
                f"Outline has {n_h2} H2 sections but only {n_sources} "
                f"source documents — adaptive cap is {adaptive_cap}. "
                f"Merge the most overlapping sections OR drop sections "
                f"with the weakest source backing. A section must be "
                f"defensible by ≥3 source docs; if it isn't, it doesn't "
                f"belong as its own H2."
            )

    # Fuzzy-dup H2 detection (SequenceMatcher ≥ 0.85 catches ~0.94 near-dupes).
    near_dupes: list[tuple[str, str, float]] = []
    n = len(outline.sections)
    for i in range(n):
        for j in range(i + 1, n):
            ratio = SequenceMatcher(
                None,
                outline.sections[i].heading.casefold(),
                outline.sections[j].heading.casefold(),
            ).ratio()
            if ratio >= params.OUTLINE_H2_FUZZY_DEDUP_THRESHOLD:
                near_dupes.append((
                    outline.sections[i].heading,
                    outline.sections[j].heading,
                    ratio,
                ))
    if near_dupes:
        sample = near_dupes[0]
        issues.append(
            f"Near-duplicate H2 section headings detected ({len(near_dupes)} "
            f"pair(s)). Example: {sample[0]!r} vs {sample[1]!r} "
            f"(similarity {sample[2]:.0%}). Merge them into a single "
            f"section OR rewrite one to cover a clearly distinct topic; "
            f"the writer will produce duplicate code/prose otherwise."
        )

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
        if s.heading.casefold() in params.BANNED_HEADINGS_LC
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

    if dag.max_stage > params.MAX_STAGE_DEPTH:
        issues.append(
            f"DAG depth {dag.max_stage} exceeds maximum "
            f"{params.MAX_STAGE_DEPTH}. Outline is too linear — flatten by "
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
            "No section has stage_index = 0 — every section claims a "
            "prerequisite. At least one section MUST have empty "
            "prerequisites (the chapter's entry point)."
        )

    return (len(issues) == 0, issues)


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
    """Build the OUTLINE_SDP prompt. target_sections_hint = adaptive per-chapter cap — over-sectioning forces code recycling stripped into hollow cross-refs."""
    return (
        f"You are the Chapter Outliner — `outline_sdp`, step 3 of the "
        f"Docs Distiller synth pipeline. Per SurveyGen-I PlanEvo "
        f"(arXiv 2508.14317 §3.1), your single job is to PRE-DECOMPOSE "
        f"the chapter into about {target_sections_hint} sections "
        f"(TARGET = {target_sections_hint}, sized to this chapter's source "
        f"pool; hard range 2-40) with EXPLICIT inter-section dependencies. "
        f"Emitting MORE than ~{target_sections_hint} sharply-distinct "
        f"sections for this chapter is almost always wrong — the extra "
        f"sections end up covering the same APIs/config as the others and "
        f"get stripped to hollow cross-references downstream. Fewer, "
        f"well-developed sections beat many overlapping ones. You write NO "
        f"prose bodies and place NO code — that happens downstream in "
        f"`sawc_write` after `digest_construct` routes the source "
        f"material to your sections.\n\n"

        f"FRAMEWORK: {framework}\n"
        f"CHAPTER: {chapter_id} — {chapter_title}\n"
        f"CHAPTER GOAL: {chapter_description}\n"
        f"VAULT SIZE (estimate): {n_vault_hashes} code blocks across "
        f"the source material below.\n\n"

        f"== SOURCE MATERIAL (already normalized; vault sentinels like "
        f"`<code-ref hash = \"...\"/>` may appear — IGNORE them, "
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
        f'    ... ~{target_sections_hint} entries (hard range 2-40) ...\n'
        f'  ]\n'
        f"}}\n\n"

        f"== HARD RULES ==\n"
        f"1. section_id format: 's' + integer ONLY — 's1', 's2', ..., "
        f"'s40'. Unique within the chapter. Once assigned, an id is "
        f"referenced by downstream nodes — do NOT renumber on subsequent "
        f"rewrites. WRONG (rejected every time): a descriptive slug like "
        f"'config-hierarchy-scopes' or 'file-level-customization' — a "
        f"human-readable name is NOT a section_id, no matter how well it "
        f"names the topic. The topic name belongs in `heading`, not here.\n"
        f"2. heading: 2-8 words, topic-y/code-y, NO leading '#'. WRONG "
        f"(rejected every time): cramming the whole section scope into "
        f"one comma-separated list, e.g. 'Extending Claude Code with "
        f"Skills, Hooks, System Prompts, and Project Context' (11 words, "
        f"reads like 4 topics glued together). RIGHT: pick the ONE most "
        f"central topic and name just that, e.g. 'Skills and Hooks' — "
        f"split the rest into their own section(s) instead of listing "
        f"them all in one heading. BANNED (case-insensitive — these are "
        f"content-types, not topics): {params.BANNED_LIST_HUMAN}.\n"
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
        f"narrative, ecosystem context, or conceptual material.\n"
        f"7. **SCOPE ORTHOGONALITY (DD-SYNTH-SECTION-RECYCLING-2026-05-29)**: "
        f"every section must teach a DISTINCT capability. Two sections that "
        f"would draw on the SAME APIs / commands / config / code examples "
        f"are ONE section — even when their headings read differently. "
        f"Before emitting, check EVERY pair of sections: if you cannot name "
        f"a concrete code example that belongs to section A but NOT to "
        f"section B, MERGE them. Anti-example (DO NOT do this): a chapter "
        f"with both 'Session Management' AND 'Remote Control' where both "
        f"cover persist / resume / InMemory store / S3 / continue — those "
        f"are the SAME scope and must be ONE section. Overlapping sections "
        f"force the writer to recycle identical code; the renderer then "
        f"strips the duplicates, leaving hollow 'see other section' "
        f"chapters. Fewer, sharply-distinct sections beat many overlapping "
        f"ones.\n\n"

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

        f"Respond ONLY with valid JSON matching the schema above. NO "
        f"prose commentary, NO markdown wrapping, NO explanation — the "
        f"JSON is parsed directly by the next graph node."
    )


def build_usc_vote_prompt(
    *,
    candidates_summary: list[dict],
    chapter_id: str,
    chapter_title: str,
    adaptive_cap: int = 0,
) -> str:
    """USC picker prompt (Brown & Cobbe 2025): structural summaries only (not full JSON) so picker stays focused on structure and context stays small."""
    lines: list[str] = []
    for i, c in enumerate(candidates_summary):
        violations = c.get("violations") or []
        viol_str = (
            f" violations = ({len(violations)}: " + "; ".join(violations[:3]) + ")"
            if violations else " violations = (none)"
        )
        headings = c.get("headings") or []
        headings_short = ", ".join(f"{h!r}" for h in headings[:6])
        if len(headings) > 6:
            headings_short += f", ... +{len(headings) - 6} more"
        lines.append(
            f"[{i}] n_sections = {c.get('n_sections')}, "
            f"max_stage = {c.get('max_stage')}, "
            f"n_stages = {c.get('n_stages')}, "
            f"avg_prereqs = {c.get('avg_prereqs', 0.0):.2f}, "
            f"n_removed_edges = {c.get('n_removed_edges', 0)}, "
            f"avg_desc_chars = {c.get('avg_desc_chars', 0):.0f}"
            f"{viol_str}\n"
            f"     headings: {headings_short}"
        )
    candidates_block = "\n".join(lines)
    cap_clause = (
        f"AT or just under {adaptive_cap} (the adaptive cap sized to this "
        f"chapter's source pool). A candidate with MORE than {adaptive_cap} "
        f"sections is over-decomposed — its extra sections will overlap the "
        f"others and be stripped to hollow cross-references; prefer the "
        f"candidate with ~{adaptive_cap} sharply-distinct sections. Fewer "
        f"is better than more here."
        if adaptive_cap
        else "near the adaptive cap for the chapter's source pool; fewer, "
             "sharply-distinct sections beat many overlapping ones"
    )
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
        f"2. Section count {cap_clause}\n"
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
    """Repair prompt for structurally-invalid outline. LLM keeps section_ids stable where possible — downstream nodes cross-reference by id."""
    issues_block = "\n".join(f"- {x}" for x in issues)
    return (
        f"Fix structural issues in this chapter outline. Keep the SAME "
        f"JSON schema (sections only). Preserve "
        f"section_ids and headings that are already good; only change "
        f"what's needed to clear the issues below. NEVER renumber "
        f"section_ids unless you're adding a new section — downstream "
        f"nodes reference them by id.\n\n"

        f"If an issue below is a `section_id` format error: the fix is "
        f"'s' + integer ONLY (e.g. 's3') — a descriptive slug is NOT a "
        f"valid section_id no matter how well it names the topic; move "
        f"that name into `heading` instead.\n"
        f"If an issue below is a `heading` length error: pick the ONE "
        f"most central topic and name just that in 2-8 words — do NOT "
        f"comma-separate multiple topics into one long heading.\n\n"

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


def summarize_candidate(
    outline: schemas.ChapterOutline, dag: schemas.OutlineDAG, issues: list[str],
) -> dict:
    """Compact structural summary for USC picker (~200 tokens per candidate); biases toward structure, not content."""
    headings = [s.heading for s in outline.sections]
    desc_chars = [len(s.description) for s in outline.sections]
    n_prereqs = [len(s.prerequisites) for s in outline.sections]
    return {
        "n_sections":      len(outline.sections),
        "max_stage":       dag.max_stage,
        "n_stages":        len(dag.stages),
        "avg_prereqs":     (sum(n_prereqs) / len(n_prereqs)) if n_prereqs else 0.0,
        "n_removed_edges": len(dag.removed_edges),
        "avg_desc_chars":  (sum(desc_chars) / len(desc_chars)) if desc_chars else 0.0,
        "headings":        headings,
        "violations":      issues,
    }


def count_vault_sentinels(md_text: str) -> int:
    """Cheap vault-size estimate for the prompt context hint."""
    return md_text.count("<code-ref hash = ")


_OUTLINE_OPTIMAL_STOPPING_ABS_FLOOR = 5

_OUTLINE_OPTIMAL_STOPPING_RATIO_OF_CAP = 0.7


def outline_optimal_stopping_min(n_sources: int | None) -> int:
    """Minimum section count for Optimal-Stopping early-exit, coupled to adaptive cap so large corpora demand more sections before short-circuiting."""
    if n_sources is None:
        return _OUTLINE_OPTIMAL_STOPPING_ABS_FLOOR
    cap = params.max_h2_for_n_sources(n_sources)
    return max(
        _OUTLINE_OPTIMAL_STOPPING_ABS_FLOOR,
        int(cap * _OUTLINE_OPTIMAL_STOPPING_RATIO_OF_CAP),
    )


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_json_response(text: str) -> Optional[dict]:
    """Best-effort JSON extraction. Tolerates ```json fences + leading
    prose. Same approach as planner/reduce/service.py."""
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except Exception:
        pass
    m = _JSON_RE.search(text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def normalize_outline_dict(raw: dict) -> dict:
    """Fix format-only violations the model reliably makes before they
    ever reach Pydantic — structured-output modes guarantee JSON shape,
    not that every string field matches an arbitrary regex, and 2026 SOTA
    practice for this class of failure is normalize-then-validate rather
    than reject-and-repair-call. Confirmed reproducible this session on
    two distinct fields: section_id as a descriptive slug instead of
    's<N>', and heading as a full comma-separated topic list instead of
    2-8 words. Best-effort only — leaves anything it can't confidently
    fix for Pydantic (and the repair loop) to catch."""
    sections = raw.get("sections")
    if not isinstance(sections, list):
        return raw

    # section_id: descriptive slug → s<position>, remapped everywhere
    # (including prerequisites) so cross-references stay consistent.
    id_remap: dict[str, str] = {}
    for i, sec in enumerate(sections):
        if not isinstance(sec, dict):
            continue
        sid = sec.get("section_id")
        if isinstance(sid, str) and not patterns.SECTION_ID_RE.match(sid):
            new_id = f"s{i + 1}"
            id_remap[sid] = new_id
            sec["section_id"] = new_id
    if id_remap:
        for sec in sections:
            if not isinstance(sec, dict):
                continue
            prereqs = sec.get("prerequisites")
            if isinstance(prereqs, list):
                sec["prerequisites"] = [
                    id_remap.get(p, p) for p in prereqs
                ]

    # heading: too many words → keep the leading clause (up to the first
    # comma/semicolon/" and "), then hard-truncate to HEADING_MAX_WORDS.
    # Lossy, but a shortened real heading beats a hard reject + repair
    # round-trip for something this cosmetic.
    for sec in sections:
        if not isinstance(sec, dict):
            continue
        heading = sec.get("heading")
        if not isinstance(heading, str):
            continue
        words = heading.split()
        if len(words) > params.HEADING_MAX_WORDS:
            head = re.split(r",| and |;", heading, maxsplit=1)[0].strip()
            head_words = head.split()
            if not (params.HEADING_MIN_WORDS <= len(head_words) <= params.HEADING_MAX_WORDS):
                # Leading clause was too short (or still too long) to use
                # as-is — fall back to a plain hard-truncate of the
                # original, which is always exactly HEADING_MAX_WORDS.
                head_words = words[:params.HEADING_MAX_WORDS]
            sec["heading"] = " ".join(head_words)

    return raw


def try_parse_outline(
    raw: dict,
) -> tuple[Optional[schemas.ChapterOutline], Optional[str]]:
    """Normalize known format-only violations, then Pydantic-validate.
    Returns (outline, error)."""
    raw = normalize_outline_dict(raw)
    try:
        outline = schemas.ChapterOutline.model_validate(raw)
        return outline, None
    except ValidationError as e:
        return None, shorten_pydantic_error(e)
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:200]}"


def shorten_pydantic_error(e: ValidationError) -> str:
    """Compact a Pydantic ValidationError into a 200-char summary that's
    still actionable in repair-prompt feedback."""
    errs = e.errors()
    if not errs:
        return "Pydantic validation failed (no detail)"
    lines = []
    for err in errs[:4]:
        loc = ".".join(str(x) for x in err.get("loc", []))
        msg = err.get("msg", "")
        lines.append(f"{loc}: {msg}")
    suffix = f" (+{len(errs) - 4} more)" if len(errs) > 4 else ""
    return "; ".join(lines) + suffix


# Public: service.py's overflow-retry path also reads this (halves the
# budget on a context-overflow batch before re-drafting).
MAX_SOURCE_CHARS = 180_000

_SOURCE_CONCAT_SEPARATOR = "\n\n---\n\n"


def concat_sources(
    bodies: list[str], *, max_chars: int = MAX_SOURCE_CHARS,
) -> tuple[str, bool]:
    """Concatenate source markdown bodies with separators, capped at
    `max_chars`, via max-min water-filling instead of sequential fill.

    `sources` upstream is `sorted(...)` by key, so a naive fill-then-stop
    silently zeroes out every source that sorts after the cap is hit —
    for any chapter whose combined body exceeds the cap, alphabetically
    -later docs never reach the outliner at all, biasing which topics get
    a section for reasons unrelated to content importance. Water-filling
    gives every source a fair, capped share of the budget instead, so a
    big/small chapter still sees the FULL topic surface, just thinner per
    doc. Original source order is preserved for readability."""
    non_empty = [b for b in bodies if b]
    if not non_empty:
        return "", False
    n = len(non_empty)
    remaining_budget = max_chars
    alloc = [0] * n
    pending = list(range(n))
    while pending and remaining_budget > 0:
        share = remaining_budget // len(pending)
        if share <= 0:
            break
        still_pending: list[int] = []
        for i in pending:
            need = len(non_empty[i]) - alloc[i]
            take = min(need, share)
            alloc[i] += take
            remaining_budget -= take
            if alloc[i] < len(non_empty[i]):
                still_pending.append(i)
        pending = still_pending
    truncated = any(alloc[i] < len(non_empty[i]) for i in range(n))
    parts = [non_empty[i][: alloc[i]] for i in range(n) if alloc[i] > 0]
    return _SOURCE_CONCAT_SEPARATOR.join(parts), truncated


_SCOPE_STOPWORDS = frozenset({
    "the", "and", "for", "this", "with", "that", "from", "section", "show",
    "how", "use", "using", "via", "your", "each", "into", "onto", "claude",
    "code", "example", "demonstrate", "learn", "cover", "when", "what",
    "where", "which", "while", "they", "them", "then", "here", "run",
    "creat", "make", "field", "valu", "option", "config", "setup", "set",
})


def scope_words(text: str) -> set[str]:
    """Lightly-stemmed content words (≥4 chars) of a heading+description,
    for lexical scope-overlap detection. Stopword-filtered."""
    out: set[str] = set()
    for t in re.findall(r"[a-z][a-z0-9_]{3,}", (text or "").lower()):
        for suf in ("ing", "tions", "tion", "ment", "ions", "ers", "es",
                    "ed", "ity", "al", "s"):
            if t.endswith(suf) and len(t) - len(suf) >= 3:
                t = t[: -len(suf)]
                break
        out.add(t)
    return out - _SCOPE_STOPWORDS


def heuristic_fallback_outline(md_text: str) -> schemas.ChapterOutline:
    """Last-resort fallback when all N samples fail to parse: derive sections from H1/H2 in source. Keeps chapter graph runnable; mgsr_replan rewrites it."""
    headings = re.findall(r"(?m)^#{1,3}\s+(.+)$", md_text or "")
    cleaned: list[str] = []
    seen: set[str] = set()
    for h in headings:
        h = h.strip().rstrip("#").strip()
        if not h:
            continue
        key = h.casefold()
        if key in seen:
            continue
        seen.add(key)
        words = h.split()
        if len(words) > 8:
            h = " ".join(words[:8])
        if key in {"introduction", "overview", "summary", "conclusion"}:
            continue
        cleaned.append(h)
        if len(cleaned) >= 8:
            break

    while len(cleaned) < 4:
        cleaned.append(f"Topic {len(cleaned) + 1}")

    sections = [
        schemas.OutlineSection(
            section_id=f"s{i + 1}",
            heading=h if len(h.split()) >= 2 else f"{h} Concepts",
            description=(
                f"Auto-derived section from source heading {h!r}; "
                "synthesized as fallback after LLM outline generation "
                "failed. Refine in MGSR."
            ),
            prerequisites=[f"s{i}"] if i > 0 else [],
            needs_code=True,
        )
        for i, h in enumerate(cleaned)
    ]
    return schemas.ChapterOutline(sections=sections)


def serialize_outline_with_dag(
    outline: schemas.ChapterOutline, dag: schemas.OutlineDAG,
) -> dict:
    """Combine outline + dag for MinIO persistence. Edges/stages are already JSON-friendly (tuples → lists)."""
    return {
        "schema_version": versions.OUTLINE_SCHEMA_VERSION,
        "prompt_version": versions.OUTLINE_PROMPT_VERSION,
        "outline":        outline.model_dump(),
        "dag": {
            "edges":         [list(e) for e in dag.edges],
            "stage_index":   dag.stage_index,
            "stages":        {str(k): v for k, v in dag.stages.items()},
            "max_stage":     dag.max_stage,
            "removed_edges": [list(e) for e in dag.removed_edges],
        },
    }


def compute_manifest_hash(
    *,
    sources: list[str],
    sources_bytes: int,
    chapter_title: str,
    chapter_description: str,
) -> str:
    payload = (
        f"sources={','.join(sorted(sources))}|"
        f"n_sources={len(sources)}|"
        f"bytes={sources_bytes}|"
        f"title={chapter_title}|"
        f"goal={chapter_description}|"
        f"prompt={versions.OUTLINE_PROMPT_VERSION}|"
        f"schema={versions.OUTLINE_SCHEMA_VERSION}"
    )
    return sha256(payload.encode("utf-8")).hexdigest()[:16]


def find_chapter(plan: dict, chapter_id: str) -> Optional[dict]:
    """Look up a chapter by id in plan-latest.json. Returns None if
    not found."""
    chapters = (plan or {}).get("chapters") or []
    for ch in chapters:
        if isinstance(ch, dict) and ch.get("id") == chapter_id:
            return ch
    return None


_CONTEXT_OVERFLOW_MARKERS = (
    "context_length", "context window", "maximum context length",
    "context_window_exceeded", "reduce the length", "too many tokens",
    "context length exceeded", "prompt is too long",
)


def is_context_overflow_error(e: Exception) -> bool:
    """Heuristic substring match — same idiom the openai-compat retry
    classifier already uses for payment/rate-limit errors. The Rotator is
    a universal, provider-agnostic gateway with no context-length-aware
    arm filtering (confirmed: no such concept exists in its routing
    code), so a large prompt CAN land on a small-context arm from a
    heterogeneous pool; the failure surfaces here as a generic exception
    indistinguishable from any other unless we pattern-match the message."""
    msg = str(e).lower()
    return any(marker in msg for marker in _CONTEXT_OVERFLOW_MARKERS)


def load_outline_payload(text: str) -> dict:
    """Parse the persisted outline blob. Returns the full payload dict;
    downstream nodes pick the fields they need (outline, dag, etc.)."""
    return json.loads(text)
