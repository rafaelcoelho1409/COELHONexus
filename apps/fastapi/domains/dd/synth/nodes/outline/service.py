from __future__ import annotations
from .keys import (
    latest_blob_key,
    latest_blob_key as _latest_blob_key,
    versioned_blob_key,
    versioned_blob_key as _versioned_blob_key,
)
from .params import (
    BANNED_HEADINGS_LC,
    BANNED_LIST_HUMAN,
    DESCRIPTION_MAX_CHARS,
    DESCRIPTION_MIN_CHARS,
    HEADING_MAX_WORDS,
    HEADING_MIN_WORDS,
    MAX_PREREQS_PER_NODE,
    MAX_STAGE_DEPTH,
    OUTLINE_ADAPTIVE_CEILING,
    OUTLINE_ADAPTIVE_DIVISOR,
    OUTLINE_ADAPTIVE_FLOOR,
    OUTLINE_H2_FUZZY_DEDUP_THRESHOLD,
    SECTIONS_MAX,
    SECTIONS_MIN,
    max_h2_for_n_sources,
)
from .patterns import SECTION_ID_RE
from .schemas import (
    ChapterOutline,
    OutlineDAG,
    OutlineSection,
)
from .versions import OUTLINE_PROMPT_VERSION, OUTLINE_SCHEMA_VERSION

import asyncio
import json
import logging
import os
import re
import time
from collections import defaultdict, deque
from difflib import SequenceMatcher
from hashlib import sha256
from typing import Optional

from pydantic import ValidationError

from domains.llm.rotator.chain import chat_judge_bandit_async
from domains.llm.rotator.chain.service import embed_via_router_async

from ....ingestion.storage import get_storage
from ...runtime.observability import record_bucket_split_overflow
from ...runtime.progress import emit_progress
from ...state import SynthState
from ..render.keys import planner_latest_key as _planner_latest_key


logger = logging.getLogger(__name__)


# DAG primitives (pure)
def build_edges(sections: list[OutlineSection]) -> list[tuple[str, str]]:
    """Edge (p, s) means p is a prerequisite of s. Unknown prereqs are
    silently skipped (validate_outline_structure flags them separately)."""
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


def derive_dag(sections: list[OutlineSection]) -> OutlineDAG:
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
    return OutlineDAG(
        edges = edges,
        stage_index = stage_index,
        stages = dict(stages),
        max_stage = max(stage_index.values()) if stage_index else 0,
        removed_edges = removed,
    )


def validate_outline_structure(
    outline: ChapterOutline,
    dag: OutlineDAG,
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
        adaptive_cap = max_h2_for_n_sources(n_sources)
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
            if ratio >= OUTLINE_H2_FUZZY_DEDUP_THRESHOLD:
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
        if s.heading.casefold() in BANNED_HEADINGS_LC
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

    if dag.max_stage > MAX_STAGE_DEPTH:
        issues.append(
            f"DAG depth {dag.max_stage} exceeds maximum "
            f"{MAX_STAGE_DEPTH}. Outline is too linear — flatten by "
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
        f"1. section_id format: 's' + integer, e.g. 's1', 's2', ..., 's40'. "
        f"Unique within the chapter. Once assigned, an id is referenced by "
        f"downstream nodes — do NOT renumber on subsequent rewrites.\n"
        f"2. heading: 2-8 words, topic-y/code-y, NO leading '#'. BANNED "
        f"(case-insensitive — these are content-types, not topics): "
        f"{BANNED_LIST_HUMAN}.\n"
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
    outline: ChapterOutline, dag: OutlineDAG, issues: list[str],
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




_N_SAMPLES               = 3

_TEMPERATURE_DRAFT       = 0.4

_TEMPERATURE_VOTE        = 0.0

_TEMPERATURE_REPAIR      = 0.2

_MAX_REPAIR_RETRIES      = 2

_MAX_TOKENS_DRAFT        = 8000

_MAX_TOKENS_VOTE         = 200

_MAX_TOKENS_REPAIR       = 8000

_MAX_SOURCE_CHARS        = 180_000

_SOURCE_CONCAT_SEPARATOR = "\n\n---\n\n"

_OUTLINE_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name":   "chapter_outline",
        "schema": ChapterOutline.model_json_schema(),
        "strict": False,
    },
}

_USC_VOTE_RESPONSE_FORMAT = {"type": "json_object"}

_OUTLINE_OPTIMAL_STOPPING_ABS_FLOOR = 5

_OUTLINE_OPTIMAL_STOPPING_RATIO_OF_CAP = 0.7

def _outline_optimal_stopping_min(n_sources: int | None) -> int:
    """Minimum section count for Optimal-Stopping early-exit, coupled to adaptive cap so large corpora demand more sections before short-circuiting."""
    if n_sources is None:
        return _OUTLINE_OPTIMAL_STOPPING_ABS_FLOOR
    # max_h2_for_n_sources already imported at module top from .params
    cap = max_h2_for_n_sources(n_sources)
    return max(
        _OUTLINE_OPTIMAL_STOPPING_ABS_FLOOR,
        int(cap * _OUTLINE_OPTIMAL_STOPPING_RATIO_OF_CAP),
    )

_OUTLINE_OPTIMAL_STOPPING_ENABLED = os.environ.get(
    "KD_OUTLINE_OPTIMAL_STOPPING", "true",
).lower() in ("true", "1", "yes", "on")

_BLOB_PREFIX = "synth"

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)

def _parse_json_response(text: str) -> Optional[dict]:
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

def _try_parse_outline(
    raw: dict,
) -> tuple[Optional[ChapterOutline], Optional[str]]:
    """Pydantic-validate raw dict → ChapterOutline. Returns (outline, error)."""
    try:
        outline = ChapterOutline.model_validate(raw)
        return outline, None
    except ValidationError as e:
        return None, _shorten_pydantic_error(e)
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:200]}"

def _shorten_pydantic_error(e: ValidationError) -> str:
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

def _concat_sources(bodies: list[str]) -> tuple[str, bool]:
    """Concatenate source markdown bodies with separators, capped at
    `_MAX_SOURCE_CHARS`. Returns (concat_text, truncated_flag)."""
    parts: list[str] = []
    total = 0
    truncated = False
    for body in bodies:
        if not body:
            continue
        if total + len(body) > _MAX_SOURCE_CHARS:
            remaining = _MAX_SOURCE_CHARS - total
            if remaining > 200:
                parts.append(body[:remaining])
                total = _MAX_SOURCE_CHARS
            truncated = True
            break
        parts.append(body)
        total += len(body) + len(_SOURCE_CONCAT_SEPARATOR)
    return _SOURCE_CONCAT_SEPARATOR.join(parts), truncated

_SCOPE_LEXICAL_JACCARD = 0.40

_SCOPE_STOPWORDS = frozenset({
    "the", "and", "for", "this", "with", "that", "from", "section", "show",
    "how", "use", "using", "via", "your", "each", "into", "onto", "claude",
    "code", "example", "demonstrate", "learn", "cover", "when", "what",
    "where", "which", "while", "they", "them", "then", "here", "run",
    "creat", "make", "field", "valu", "option", "config", "setup", "set",
})
# Threshold for _detect_semantic_h2_duplicates — must be defined BEFORE
# that function (used as a default arg, evaluated at module-init).
_SEMANTIC_H2_DEDUP_THRESHOLD = 0.74

def _scope_words(text: str) -> set[str]:
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

async def _detect_semantic_h2_duplicates(
    outline: ChapterOutline,
    *,
    threshold: float = _SEMANTIC_H2_DEDUP_THRESHOLD,
) -> list[str]:
    """Return issues for scope-duplicate H2 pairs (embedding cosine OR lexical overlap). Fail-soft: lexical pass still runs when embedder is unavailable."""
    sections = outline.sections
    if len(sections) <= 1:
        return []
    n = len(sections)
    words = [_scope_words(f"{s.heading} {s.description}") for s in sections]

    # Embedding cosine (semantic signal) — best-effort.
    sim = None
    try:
        embeddings = await embed_via_router_async(
            [f"{s.heading}\n{s.description}" for s in sections],
            input_type="query",
        )
        import numpy as np
        embs = np.array(embeddings, dtype=np.float32)
        norms = np.linalg.norm(embs, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        normed = embs / norms
        sim = normed @ normed.T
    except Exception as e:
        logger.warning(
            f"[outline_sdp] semantic H2 dedup embed/cosine failed: "
            f"{type(e).__name__}: {e} — falling back to lexical scope check"
        )
        sim = None

    flagged: list[tuple[str, str, float, str]] = []
    for i in range(n):
        for j in range(i + 1, n):
            cos = float(sim[i, j]) if sim is not None else 0.0
            wi, wj = words[i], words[j]
            jac = (len(wi & wj) / len(wi | wj)) if (wi or wj) else 0.0
            if cos >= threshold or jac >= _SCOPE_LEXICAL_JACCARD:
                flagged.append((
                    sections[i].heading, sections[j].heading,
                    max(cos, jac),
                    "cosine" if cos >= threshold else "lexical",
                ))
    if not flagged:
        return []
    pair_strs = [
        f"{a!r} ↔ {b!r} ({s:.0%} {w})" for a, b, s, w in flagged[:3]
    ]
    suffix = (
        f", +{len(flagged) - 3} more pairs" if len(flagged) > 3 else ""
    )
    return [
        f"Scope-duplicate H2 section pairs detected ({len(flagged)} "
        f"pair(s); embedding cosine ≥ {threshold:.0%} OR content-word "
        f"overlap ≥ {_SCOPE_LEXICAL_JACCARD:.0%}): "
        f"{', '.join(pair_strs)}{suffix}. These sections cover the SAME "
        f"scope (same APIs / examples) with different wording — MERGE each "
        f"pair into ONE section under a unified heading, OR re-scope one to "
        f"a genuinely distinct capability. Overlapping sections make the "
        f"writer recycle code; the renderer then strips the duplicates, "
        f"leaving hollow 'see other section' sections."
    ]

def _heuristic_fallback_outline(md_text: str) -> ChapterOutline:
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
        OutlineSection(
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
    return ChapterOutline(sections=sections)

def _serialize_outline_with_dag(
    outline: ChapterOutline, dag: OutlineDAG,
) -> dict:
    """Combine outline + dag for MinIO persistence. Edges/stages are already JSON-friendly (tuples → lists)."""
    return {
        "schema_version": OUTLINE_SCHEMA_VERSION,
        "prompt_version": OUTLINE_PROMPT_VERSION,
        "outline":        outline.model_dump(),
        "dag": {
            "edges":         [list(e) for e in dag.edges],
            "stage_index":   dag.stage_index,
            "stages":        {str(k): v for k, v in dag.stages.items()},
            "max_stage":     dag.max_stage,
            "removed_edges": [list(e) for e in dag.removed_edges],
        },
    }

async def _generate_samples(
    prompt: str, n: int, thread_id: str,
    *,
    n_sources: int | None = None,
) -> list[tuple[dict, dict]]:
    """Fire N drafts with Optimal-Stopping (arXiv 2510.01394): sample 1 checked first; if clean + valid + ≥ min sections, skip remaining N-1. Else fan out concurrently, then USC vote. Disabled via KD_OUTLINE_OPTIMAL_STOPPING=false."""
    if _OUTLINE_OPTIMAL_STOPPING_ENABLED and n >= 2:
        r0 = await _draft_one_outline(
            prompt, sample_idx=0, n_total=n, thread_id=thread_id,
        )
        results: list = [r0]
        parsed0, _meta0 = r0
        if parsed0 is not None:
            outline0, _err = _try_parse_outline(parsed0)
            if outline0 is not None:
                dag0 = derive_dag(outline0.sections)
                _, issues0 = validate_outline_structure(
                    outline0, dag0, n_sources=n_sources,
                )
                if (
                    not issues0
                    and len(outline0.sections)
                        >= _outline_optimal_stopping_min(n_sources)
                ):
                    logger.info(
                        f"[outline_sdp] Optimal-Stopping fired — sample 0 "
                        f"clean ({len(outline0.sections)} sections, 0 issues); "
                        f"skipping remaining {n - 1} samples"
                    )
                    successful: list[tuple[dict, dict]] = []
                    if parsed0 is not None:
                        successful.append(r0)
                    return successful
        remaining = await asyncio.gather(*[
            _draft_one_outline(
                prompt, sample_idx=i, n_total=n, thread_id=thread_id,
            )
            for i in range(1, n)
        ])
        results.extend(remaining)
    else:
        results = await asyncio.gather(*[
            _draft_one_outline(
                prompt, sample_idx=i, n_total=n, thread_id=thread_id,
            )
            for i in range(n)
        ])
    successful: list[tuple[dict, dict]] = []
    for parsed, meta in results:
        if parsed is not None:
            successful.append((parsed, meta))
        else:
            logger.info(
                f"[outline_sdp] draft failed: {meta.get('error', 'unknown')}"
            )
    return successful

async def _usc_pick(
    candidates: list[tuple[ChapterOutline, OutlineDAG, list[str]]],
    chapter_id: str,
    chapter_title: str,
    adaptive_cap: int,
) -> int:
    """Run USC picker over candidates. adaptive_cap = per-chapter section ceiling; picker rewards candidates at or just under it. Falls back to index 0 on failure."""
    if len(candidates) <= 1:
        return 0
    summaries = [
        summarize_candidate(o, d, issues)
        for (o, d, issues) in candidates
    ]
    prompt = build_usc_vote_prompt(
        candidates_summary=summaries,
        chapter_id=chapter_id,
        chapter_title=chapter_title,
        adaptive_cap=adaptive_cap,
    )
    try:
        response, _ = await chat_judge_bandit_async(
            prompt,
            max_tokens=_MAX_TOKENS_VOTE,
            temperature=_TEMPERATURE_VOTE,
            response_format=_USC_VOTE_RESPONSE_FORMAT,
        )
        parsed = _parse_json_response(response)
        if parsed and "chosen_index" in parsed:
            idx = int(parsed["chosen_index"])
            if 0 <= idx < len(candidates):
                return idx
    except Exception as e:
        logger.warning(
            f"[outline_sdp] USC picker failed: "
            f"{type(e).__name__}: {e} — falling back to first candidate"
        )
    return 0

def _compute_manifest_hash(
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
        f"prompt={OUTLINE_PROMPT_VERSION}|"
        f"schema={OUTLINE_SCHEMA_VERSION}"
    )
    return sha256(payload.encode("utf-8")).hexdigest()[:16]

def _find_chapter(plan: dict, chapter_id: str) -> Optional[dict]:
    """Look up a chapter by id in plan-latest.json. Returns None if
    not found."""
    chapters = (plan or {}).get("chapters") or []
    for ch in chapters:
        if isinstance(ch, dict) and ch.get("id") == chapter_id:
            return ch
    return None



async def _draft_one_outline(
    prompt: str,
    *,
    sample_idx: int,
    n_total: int,
    thread_id: str,
) -> tuple[Optional[dict], dict]:
    """One LLM call for outline draft. Emits `sample_done` SSE per sample so UI shows per-sample progress during asyncio.gather (otherwise silent for ~30s)."""
    t0 = time.monotonic()
    try:
        response, meta = await chat_judge_bandit_async(
            prompt,
            max_tokens=_MAX_TOKENS_DRAFT,
            temperature=_TEMPERATURE_DRAFT,
            response_format=_OUTLINE_RESPONSE_FORMAT,
        )
    except Exception as e:
        await emit_progress(
            thread_id, "outline_sdp", "sample_done",
            sample_idx=sample_idx, n_total=n_total,
            ok=False, error=f"{type(e).__name__}: {str(e)[:120]}",
            wall_ms=int((time.monotonic() - t0) * 1000),
        )
        return None, {"error": f"{type(e).__name__}: {str(e)[:200]}"}
    parsed = _parse_json_response(response)
    if not parsed:
        await emit_progress(
            thread_id, "outline_sdp", "sample_done",
            sample_idx=sample_idx, n_total=n_total,
            ok=False, error="parse_failed",
            wall_ms=int((time.monotonic() - t0) * 1000),
            deployment=meta.get("deployment"),
        )
        return None, {
            **meta,
            "error": "parse_failed",
            "raw":   (response or "")[:200],
        }
    await emit_progress(
        thread_id, "outline_sdp", "sample_done",
        sample_idx=sample_idx, n_total=n_total,
        ok=True,
        wall_ms=int((time.monotonic() - t0) * 1000),
        deployment=meta.get("deployment"),
        n_sections=len(parsed.get("sections") or []),
    )
    return parsed, meta

async def outline_sdp_run(state: SynthState) -> dict:
    """Run the Structure-Driven Planner for one chapter."""
    slug = state.get("framework_slug")
    chapter_id = state.get("chapter_id")
    thread_id = state.get("thread_id") or ""

    if not slug or not chapter_id:
        return {
            "outline_path":  "",
            "outline_stats": {
                "skipped": "no_slug_or_chapter_id",
                "wall_ms": 0,
            },
            "status": "failed",
            "error":  "framework_slug or chapter_id missing from SynthState",
        }

    t0 = time.monotonic()
    minio = get_storage()

    plan_key = _planner_latest_key(slug)
    if not await minio.exists(plan_key):
        return {
            "outline_path":  "",
            "outline_stats": {
                "skipped": "plan_not_found",
                "plan_key": plan_key,
                "wall_ms": int((time.monotonic() - t0) * 1000),
            },
            "status": "failed",
            "error":  f"planner plan {plan_key!r} not in MinIO; run planner first",
        }

    plan_text = await minio.read_text(plan_key)
    try:
        plan = json.loads(plan_text)
    except Exception as e:
        return {
            "outline_path":  "",
            "outline_stats": {
                "skipped": "plan_unreadable",
                "wall_ms": int((time.monotonic() - t0) * 1000),
            },
            "status": "failed",
            "error":  f"plan-latest.json unreadable: {type(e).__name__}: {e}",
        }

    chapter = _find_chapter(plan, chapter_id)
    if chapter is None:
        return {
            "outline_path":  "",
            "outline_stats": {
                "skipped":     "chapter_not_in_plan",
                "wall_ms":     int((time.monotonic() - t0) * 1000),
                "known_ids":   [c.get("id") for c in (plan.get("chapters") or [])],
            },
            "status": "failed",
            "error":  f"chapter {chapter_id!r} not in plan-latest.json",
        }

    chapter_title       = chapter.get("title") or chapter_id
    chapter_description = chapter.get("description") or ""
    sources             = sorted(chapter.get("sources") or [])
    if not sources:
        return {
            "outline_path":  "",
            "outline_stats": {
                "skipped": "no_sources",
                "wall_ms": int((time.monotonic() - t0) * 1000),
            },
            "status": "failed",
            "error":  f"chapter {chapter_id!r} has zero sources in plan",
        }

    await emit_progress(
        thread_id, "outline_sdp", "start",
        chapter_id = chapter_id,
        chapter_title = chapter_title,
        n_sources = len(sources),
    )

    # Each source is already corpus_normalized + vault_sentinelized by
    # ingestion (architecture cleanup). We just concat.
    bodies = await minio.read_many(sources)
    bodies = [b for b in bodies if b]
    if not bodies:
        return {
            "outline_path":  "",
            "outline_stats": {
                "skipped": "source_bodies_empty",
                "wall_ms": int((time.monotonic() - t0) * 1000),
            },
            "status": "failed",
            "error":  "all source bodies came back empty",
        }
    sources_concat_md, truncated = _concat_sources(bodies)
    n_vault_hashes = count_vault_sentinels(sources_concat_md)

    await emit_progress(
        thread_id, "outline_sdp", "sources_loaded",
        n_sources = len(sources),
        n_bodies = len(bodies),
        bytes = len(sources_concat_md),
        truncated = truncated,
        n_vault_hashes = n_vault_hashes,
    )

    manifest_hash = _compute_manifest_hash(
        sources = sources,
        sources_bytes = len(sources_concat_md),
        chapter_title = chapter_title,
        chapter_description = chapter_description,
    )
    versioned_key = _versioned_blob_key(slug, chapter_id, manifest_hash)
    latest_key    = _latest_blob_key(slug, chapter_id)

    if await minio.exists(versioned_key) and await minio.exists(latest_key):
        try:
            cached_text = await minio.read_text(versioned_key)
            cached = json.loads(cached_text)
            outline_dict = (cached or {}).get("outline") or {}
            dag_dict     = (cached or {}).get("dag") or {}
            elapsed = int((time.monotonic() - t0) * 1000)
            stats = {
                "n_sections":   len(outline_dict.get("sections") or []),
                "max_stage":    int(dag_dict.get("max_stage", 0)),
                "n_stages":     len(dag_dict.get("stages") or {}),
                "n_removed_edges": len(dag_dict.get("removed_edges") or []),
                "wall_ms":      elapsed,
                "store_path":   latest_key,
                "versioned_path": versioned_key,
                "manifest_hash":  manifest_hash,
                "cache_hit":    True,
                "prompt_version": cached.get("prompt_version"),
            }
            await emit_progress(
                thread_id, "outline_sdp", "done",
                n_sections = stats["n_sections"],
                max_stage = stats["max_stage"],
                wall_ms = elapsed, cache_hit = True,
            )
            logger.info(
                f"[outline_sdp] {slug}/{chapter_id}: CACHE HIT — "
                f"{stats['n_sections']} sections, max_stage = "
                f"{stats['max_stage']}, {elapsed} ms"
            )
            return {"outline_path": latest_key, "outline_stats": stats}
        except Exception as e:
            logger.warning(
                f"[outline_sdp] {slug}/{chapter_id}: cached blob "
                f"{versioned_key!r} unreadable ({type(e).__name__}: {e}); "
                f"recomputing"
            )

    # Adaptive cap (not fixed 8): old fixed-8 pushed LLM to over-section small chapters (then hard-trimmed or deadlocked). Correct count up front → winner rarely needs trimming.
    adaptive_target = max_h2_for_n_sources(len(sources))
    prompt = build_outline_prompt(
        framework = slug,
        chapter_id = chapter_id,
        chapter_title = chapter_title,
        chapter_description = chapter_description,
        n_vault_hashes = n_vault_hashes,
        sources_concat_md = sources_concat_md,
        target_sections_hint = adaptive_target,
    )
    raw_samples = await _generate_samples(
        prompt, _N_SAMPLES, thread_id, n_sources = len(sources),
    )

    await emit_progress(
        thread_id, "outline_sdp", "samples_drafted",
        n_samples = len(raw_samples), n_requested = _N_SAMPLES,
    )

    candidates: list[tuple[ChapterOutline, OutlineDAG, list[str]]] = []
    pydantic_failures = 0
    for parsed_dict, meta in raw_samples:
        outline, err = _try_parse_outline(parsed_dict)
        if outline is None:
            pydantic_failures += 1
            logger.info(
                f"[outline_sdp] {slug}/{chapter_id}: pydantic-reject — {err}"
            )
            continue
        dag = derive_dag(outline.sections)
        _, issues = validate_outline_structure(
            outline, dag, n_sources = len(sources),
        )
        candidates.append((outline, dag, issues))

    await emit_progress(
        thread_id, "outline_sdp", "samples_validated",
        n_candidates = len(candidates), n_pydantic_fail = pydantic_failures,
    )

    if not candidates:
        logger.warning(
            f"[outline_sdp] {slug}/{chapter_id}: ALL {_N_SAMPLES} samples "
            f"failed to parse; emitting heuristic fallback outline"
        )
        outline = _heuristic_fallback_outline(sources_concat_md)
        dag = derive_dag(outline.sections)
        candidates = [(outline, dag, ["heuristic_fallback"])]

    chosen_idx = await _usc_pick(
        candidates, chapter_id, chapter_title, adaptive_cap = adaptive_target,
    )
    outline, dag, issues = candidates[chosen_idx]

    # Embed heading+description pairs above similarity threshold → repair-loop feedback. Fail-soft.
    semantic_dupe_issues = await _detect_semantic_h2_duplicates(outline)
    if semantic_dupe_issues:
        issues = list(issues) + semantic_dupe_issues
        logger.info(
            f"[outline_sdp] {slug}/{chapter_id}: semantic H2 dedup found "
            f"{len(semantic_dupe_issues)} feedback message(s); will drive "
            f"repair loop"
        )

    await emit_progress(
        thread_id, "outline_sdp", "usc_voted",
        chosen_index = chosen_idx, n_initial_violations = len(issues),
    )

    n_repairs = 0
    for attempt in range(_MAX_REPAIR_RETRIES):
        if not issues:
            break
        n_repairs += 1
        await emit_progress(
            thread_id, "outline_sdp", "repair_attempt",
            attempt = attempt + 1,
            n_violations = len(issues),
        )
        repair_prompt = build_repair_prompt(
            framework = slug,
            chapter_id = chapter_id,
            chapter_title = chapter_title,
            chapter_description = chapter_description,
            current_outline_json = json.dumps(outline.model_dump(), indent = 2),
            issues = issues,
            sources_concat_md = sources_concat_md,
        )
        try:
            repair_response, _ = await chat_judge_bandit_async(
                repair_prompt,
                max_tokens = _MAX_TOKENS_REPAIR,
                temperature = _TEMPERATURE_REPAIR,
                response_format = _OUTLINE_RESPONSE_FORMAT,
            )
            parsed = _parse_json_response(repair_response)
            if not parsed:
                logger.warning(
                    f"[outline_sdp] {slug}/{chapter_id}: repair attempt "
                    f"{attempt + 1} produced unparseable JSON; keeping prior"
                )
                continue
            new_outline, err = _try_parse_outline(parsed)
            if new_outline is None:
                logger.warning(
                    f"[outline_sdp] {slug}/{chapter_id}: repair attempt "
                    f"{attempt + 1} pydantic-rejected: {err}"
                )
                continue
            new_dag = derive_dag(new_outline.sections)
            _, new_issues = validate_outline_structure(
                new_outline, new_dag, n_sources = len(sources),
            )
            # Re-check semantic H2 dedup on repaired outline so repair loop credits the LLM's response to feedback.
            new_semantic = await _detect_semantic_h2_duplicates(new_outline)
            if new_semantic:
                new_issues = list(new_issues) + new_semantic
            # Only accept if it ACTUALLY improves things.
            if len(new_issues) <= len(issues):
                outline = new_outline
                dag = new_dag
                issues = new_issues
        except Exception as e:
            logger.warning(
                f"[outline_sdp] {slug}/{chapter_id}: repair attempt "
                f"{attempt + 1} failed: {type(e).__name__}: {e}"
            )
            continue

    # HARD-TRIM: LLM ignores soft cap signal (BU ch-02 shipped 30 H2 at cap 12; CC ch-01 shipped 20 at cap 14). Trim to adaptive_cap directly — old max(SECTIONS_MIN, cap) floor caused NO-OP for 4-section outlines below cap 4.
    adaptive_cap = max_h2_for_n_sources(len(sources))
    if len(outline.sections) > adaptive_cap:
        n_before = len(outline.sections)
        # Topological order: lower stage_index first.
        sections_by_stage: list[tuple[int, OutlineSection]] = []
        sid_to_section = {s.section_id: s for s in outline.sections}
        for stage_idx in sorted(dag.stages.keys()):
            for sid in dag.stages[stage_idx]:
                if sid in sid_to_section:
                    sections_by_stage.append((stage_idx, sid_to_section[sid]))
        # Sections not in any stage (orphans) appended last.
        seen = {s.section_id for _, s in sections_by_stage}
        for s in outline.sections:
            if s.section_id not in seen:
                sections_by_stage.append((dag.max_stage + 1, s))
        kept = [s for _, s in sections_by_stage[:adaptive_cap]]
        kept_ids = {s.section_id for s in kept}
        # Clean prereqs that point to dropped sections.
        for s in kept:
            s.prerequisites = [p for p in s.prerequisites if p in kept_ids]
        outline = outline.model_copy(update = {"sections": kept})
        dag = derive_dag(outline.sections)
        logger.warning(
            f"[outline_sdp] {slug}/{chapter_id}: HARD-TRIM outline "
            f"{n_before} → {len(outline.sections)} sections (adaptive_cap = "
            f"{adaptive_cap}, n_sources = {len(sources)}). LLM ignored the "
            f"soft cap signal after {n_repairs} repairs; programmatic "
            f"trim restores the bound."
        )
        record_bucket_split_overflow(
            framework = slug,
            sections_dropped = max(n_before - len(outline.sections), 0),
        )
        # Re-validate post-trim so downstream sees actual remaining violations (not pre-trim ones including the now-resolved cap-exceeded).
        _, issues = validate_outline_structure(
            outline, dag, n_sources = len(sources),
        )
        # Re-check semantic dedup post-trim (trimming may have removed near-duplicate H2s).
        post_trim_semantic = await _detect_semantic_h2_duplicates(outline)
        if post_trim_semantic:
            issues = list(issues) + post_trim_semantic
        await emit_progress(
            thread_id, "outline_sdp", "hard_trimmed",
            n_before = n_before, n_after = len(outline.sections),
            adaptive_cap = adaptive_cap, n_sources = len(sources),
        )

    final_violations = issues

    payload = _serialize_outline_with_dag(outline, dag)
    payload["framework_slug"]   = slug
    payload["chapter_id"]       = chapter_id
    payload["chapter_title"]    = chapter_title
    payload["manifest_hash"]    = manifest_hash
    payload["source_keys"]      = sources
    payload["n_vault_hashes"]   = n_vault_hashes
    payload["truncated"]        = truncated
    payload["n_repairs"]        = n_repairs
    payload["final_violations"] = final_violations

    blob_bytes = json.dumps(payload, indent = 2, ensure_ascii = False)
    await minio.write(
        versioned_key, blob_bytes, content_type = "application/json",
    )
    await minio.write(
        latest_key, blob_bytes, content_type = "application/json",
    )

    elapsed = int((time.monotonic() - t0) * 1000)
    stats = {
        "n_sections":     len(outline.sections),
        "max_stage":      dag.max_stage,
        "n_stages":       len(dag.stages),
        "n_removed_edges": len(dag.removed_edges),
        "n_repairs":      n_repairs,
        "n_violations":   len(final_violations),
        "violations":     final_violations,
        "n_samples":      len(candidates),
        "n_vault_hashes": n_vault_hashes,
        "truncated":      truncated,
        "wall_ms":        elapsed,
        "store_path":     latest_key,
        "versioned_path": versioned_key,
        "manifest_hash":  manifest_hash,
        "cache_hit":      False,
        "prompt_version": OUTLINE_PROMPT_VERSION,
    }
    await emit_progress(
        thread_id, "outline_sdp", "done",
        n_sections = stats["n_sections"],
        max_stage = stats["max_stage"],
        n_repairs = n_repairs,
        n_violations = stats["n_violations"],
        wall_ms = elapsed,
    )
    logger.info(
        f"[outline_sdp] {slug}/{chapter_id}: {stats['n_sections']} "
        f"sections, max_stage = {stats['max_stage']}, "
        f"n_stages = {stats['n_stages']}, n_repairs = {n_repairs}, "
        f"violations = {len(final_violations)}, {elapsed} ms"
    )
    return {"outline_path": latest_key, "outline_stats": stats}


# Convenience loader for downstream nodes
def load_outline_payload(text: str) -> dict:
    """Parse the persisted outline blob. Returns the full payload dict;
    downstream nodes pick the fields they need (outline, dag, etc.)."""
    return json.loads(text)
