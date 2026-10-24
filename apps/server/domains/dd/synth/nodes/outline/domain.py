"""outline_sdp — pure helpers: DAG primitives, structural validation, prompt
builders, JSON/Pydantic parsing, source concatenation, and manifest hashing.
No I/O, no LLM calls, no logging — see service.py for the orchestration
(LLM sampling, USC voting, semantic dedup, MinIO persistence) that calls
these."""
from __future__ import annotations
import domains
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


def flatten_prerequisites(outline: schemas.ChapterOutline, dag: schemas.OutlineDAG, max_stage: int) -> schemas.ChapterOutline:
    """Deterministic repair for a prerequisite chain deeper than `max_stage` (fastmcp ch-01: s1→s2→…→s12, DAG depth 11, so the writer ran its stages one section at
    a time: 25 min against 10). A section deeper than the cap keeps its prerequisites but lifted to their nearest ancestor that sits above the last stage; sections
    within the cap are untouched. Section ids, headings and order never change."""
    stage = dag.stage_index
    by_id = {sec.section_id: sec for sec in outline.sections}

    def lift(pid: str) -> str:
        seen: set[str] = set()
        while stage.get(pid, 0) > max_stage - 1 and pid not in seen:
            seen.add(pid)
            parents = [q for q in by_id[pid].prerequisites if q in by_id and q != pid]
            if not parents:
                break
            pid = max(parents, key=lambda q: stage.get(q, 0))
        return pid

    sections = []
    for sec in outline.sections:
        if stage.get(sec.section_id, 0) > max_stage:
            lifted = list(dict.fromkeys(lift(p) for p in sec.prerequisites if p in by_id))
            sec = sec.model_copy(update={"prerequisites": lifted})
        sections.append(sec)
    return outline.model_copy(update={"sections": sections})


def validate_outline_structure(
    outline: schemas.ChapterOutline,
    dag: schemas.OutlineDAG,
    *,
    n_sources: Optional[int] = None,
    n_blocks: int = 0,
) -> tuple[bool, list[str]]:
    """(ok, issues): cross-section checks Pydantic can't handle (unique ids/headings, banned headings, valid prereqs, DAG depth cap, fuzzy-dup H2, adaptive section count)."""
    issues: list[str] = []
    ids = [s.section_id for s in outline.sections]
    headings_lc = [s.heading.casefold() for s in outline.sections]

    # Adaptive cap from source pool size; falls back to SECTIONS_MAX
    # ceiling when n_sources is None.
    if n_sources is not None:
        adaptive_cap = params.max_h2_for_n_sources(n_sources, n_blocks)
        n_h2 = len(outline.sections)
        floor = params.min_h2_for_n_blocks(n_blocks, adaptive_cap)
        if floor and n_h2 < floor:
            issues.append(
                f"{params.FLOOR_ISSUE_PREFIX}{n_h2} H2 sections but the chapter's sources hold {n_blocks} code blocks, and one section can show "
                f"at most {params.SECTION_SUBTOPIC_MAX}. Split it into AT LEAST {floor} sections (at most "
                f"{adaptive_cap}), each a sharply distinct capability or client that the sources actually cover (never add a topic they do not), so the code can actually be shown."
            )
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


def fallback_candidate_index(
    candidates: list[tuple[schemas.ChapterOutline, schemas.OutlineDAG, list[str]]], adaptive_cap: int,
) -> int:
    """The candidate to ship when the USC picker is unavailable: fewest validation issues, then the most sections up to the cap
    (a short outline of a code-heavy chapter loses blocks), then the earliest. The picker 504'd in five runs in a row and the old
    fallback always took candidate 0."""
    return min(
        range(len(candidates)),
        key = lambda i: (len(candidates[i][2]), -min(len(candidates[i][0].sections), adaptive_cap), i),
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


def is_floor_issue(issue: str) -> bool:
    """True for the validation issue raised when a code-heavy chapter has fewer sections than its block floor."""
    return issue.startswith(params.FLOOR_ISSUE_PREFIX)


def source_titles(bodies: list[str], limit: int = 30) -> list[str]:
    """First H1 of each source body (clipped), in source order: the natural seams to split an under-sectioned outline along."""
    titles: list[str] = []
    for body in bodies:
        for line in (body or "").splitlines():
            if line.startswith("# "):
                title = line[2:].strip()[:70]
                if title and title not in titles:
                    titles.append(title)
                break
        if len(titles) >= limit:
            break
    return titles


def accept_repair(issues: list[str], new_issues: list[str], slack: int) -> bool:
    """A repaired outline replaces the current one when it leaves no more issues than before, or when it clears the section-count floor at the cost of at most `slack` extra
    issues: the repair model usually overshoots (qdrant ch-10: 5 sections for 160 blocks -> 20 sections with 2 issues, rejected, and the same 5-section outline came back on every
    later attempt), and a too-long outline is something the next repair can merge down while a too-short one never grew."""
    if len(new_issues) <= len(issues):
        return True
    cleared = any(is_floor_issue(x) for x in issues) and not any(is_floor_issue(x) for x in new_issues)
    return cleared and len(new_issues) <= len(issues) + slack


def floor_repair_hint(bodies: list[str]) -> str:
    """Extra repair-prompt text for an unmet section floor: the source titles, so the split follows the material."""
    titles = source_titles(bodies)
    if not titles:
        return ""
    return " The chapter's sources are titled: " + "; ".join(titles) + ". Give each distinct client, tool or capability among them its own section, and add no section on a topic none of them covers."


def count_code_blocks(bodies: list[str]) -> int:
    """Distinct code blocks across the chapter's source bodies: the vault the digest will build. Raw bodies are vaulted the way the digest does it; already-sentinelized bodies count their sentinels."""
    hashes: set[str] = set()
    for body in bodies:
        if not body:
            continue
        found = [
            m.group("hash")
            for m in domains.dd.synth.nodes.vault.patterns.SENTINEL_RE.finditer(body)
        ]
        if found:
            hashes.update(found)
            continue
        try:
            _, entries = domains.dd.synth.nodes.vault.domain.sentinelize_doc(body)
        except Exception:
            continue
        hashes.update(entries)
    return len(hashes)


def outline_optimal_stopping_min(n_sources: int | None, n_blocks: int = 0) -> int:
    """Minimum section count for Optimal-Stopping early-exit, coupled to adaptive cap so large corpora demand more sections before short-circuiting."""
    if n_sources is None:
        return params.OUTLINE_OPTIMAL_STOPPING_ABS_FLOOR
    cap = params.max_h2_for_n_sources(n_sources, n_blocks)
    return max(
        params.OUTLINE_OPTIMAL_STOPPING_ABS_FLOOR,
        int(cap * params.OUTLINE_OPTIMAL_STOPPING_RATIO_OF_CAP),
    )


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
    m = patterns.JSON_RE.search(text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def clean_heading(text: str) -> str:
    """Strip markup debris from a heading: markdown links (permalink anchors
    like `[](#id "Link to this heading")` vanish, `[text](url)` keeps its
    text), `¶`/icon-font glyphs, backslash escapes, trailing `#`s, and runs
    of whitespace. Plain headings pass through unchanged."""
    h = patterns.HEADING_MD_LINK_RE.sub(lambda m: m.group(1), text or "")
    h = patterns.HEADING_ICON_GLYPH_RE.sub("", h)
    h = patterns.HEADING_MD_ESCAPE_RE.sub(r"\1", h)
    h = h.strip().rstrip("#").strip()
    return " ".join(h.split())


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
        heading = clean_heading(heading)
        sec["heading"] = heading
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


def concat_sources(
    bodies: list[str], *, max_chars: int = params.MAX_SOURCE_CHARS,
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
    return params.SOURCE_CONCAT_SEPARATOR.join(parts), truncated


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
    return out - params.SCOPE_STOPWORDS


def _source_headings(md_text: str) -> list[str]:
    """Distinct, cleaned H1-H3 headings of the concatenated sources, in
    document order. Fence-aware (a `# comment` inside a code block is not a
    heading) with the fence state reset at each source boundary, since
    `concat_sources` can cut a body mid-fence. Banned content-type headings
    ('Overview', ...) and empty-after-cleaning ones are skipped."""
    out: list[str] = []
    seen: set[str] = set()
    for body in (md_text or "").split(params.SOURCE_CONCAT_SEPARATOR):
        in_fence = False
        for line in body.split("\n"):
            if patterns.FENCE_LINE_RE.match(line):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            m = patterns.ATX_HEADING_RE.match(line)
            if not m:
                continue
            h = clean_heading(m.group(1))
            key = h.casefold()
            if not h or key in seen or key in params.BANNED_HEADINGS_LC:
                continue
            seen.add(key)
            words = h.split()
            out.append(" ".join(words[:params.HEADING_MAX_WORDS]))
    return out


def heuristic_fallback_outline(
    md_text: str, *, max_sections: int = 8,
) -> schemas.ChapterOutline:
    """Last-resort fallback when all N samples fail to parse: derive sections from the H1-H3 headings in the sources, spread evenly across the whole corpus (the first N headings alone only ever cover the first page or two). Sections are independent (no prerequisite chain), so the DAG stays flat and writable in parallel. Keeps the chapter graph runnable."""
    headings = _source_headings(md_text)
    n_target = max(params.SECTIONS_MIN, max_sections)
    if len(headings) > n_target:
        step = len(headings) / n_target
        headings = [headings[int(i * step)] for i in range(n_target)]

    while len(headings) < params.SECTIONS_MIN:
        headings.append(f"Topic {len(headings) + 1}")

    sections = [
        schemas.OutlineSection(
            section_id=f"s{i + 1}",
            heading=h,
            description=(
                f"Auto-derived section from source heading {h!r}; "
                f"{params.FALLBACK_DESCRIPTION_TAG}."
            ),
            prerequisites=[],
            needs_code=True,
        )
        for i, h in enumerate(headings)
    ]
    return schemas.ChapterOutline(sections=sections)


def is_heuristic_fallback_outline(outline_dict: dict) -> bool:
    """True when every section still carries the fallback's description tag — i.e. no LLM draft or repair ever replaced it. Works on cached blobs written before this check existed."""
    sections = (outline_dict or {}).get("sections") or []
    return bool(sections) and all(
        isinstance(s, dict)
        and params.FALLBACK_DESCRIPTION_TAG in (s.get("description") or "")
        for s in sections
    )


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


def is_context_overflow_error(e: Exception) -> bool:
    """Heuristic substring match — same idiom the openai-compat retry
    classifier already uses for payment/rate-limit errors. The Rotator is
    a universal, provider-agnostic gateway with no context-length-aware
    arm filtering (confirmed: no such concept exists in its routing
    code), so a large prompt CAN land on a small-context arm from a
    heterogeneous pool; the failure surfaces here as a generic exception
    indistinguishable from any other unless we pattern-match the message."""
    msg = str(e).lower()
    return any(marker in msg for marker in params.CONTEXT_OVERFLOW_MARKERS)
