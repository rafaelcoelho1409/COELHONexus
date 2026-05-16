"""
OP-HIERARCHICAL-SYNTH (2026-04-26, Round 2 post-Run-20)

4-phase chapter synthesis pipeline that replaces monolithic synth on chapters
whose vault exceeds the constraint-vs-prose attention-competition cliff
(empirically ~50 hashes per Run-16 / Run-20 evidence; see roadmap doc).

Phases:
  A. Outline (one prose-only LLM call) — produce 4-15 OutlineSections with
     headings, goals, and cross-section contracts. Also produces holistic
     challenges + flashcards. NO code_refs, no enum constraint here.
  B. Hash routing (deterministic, no LLM) — embed each vault hash's local
     context (preceding heading + first code line) and each OutlineSection's
     (heading + goal). Cosine-similarity matrix → assign each hash to its
     argmax section. Identify shared_core hashes (high-entropy = roughly
     equally relevant to all sections; emitted in every section's prompt).
     Hard-cap orphan rate (max-similarity below threshold) at 5%; if
     exceeded, raise HierarchicalSynthFailed → caller falls back to flat synth.
  C. Per-section parallel synth (asyncio.gather over the existing
     `_invoke_structured_with_fallback`). Each call's enum is small
     (assigned ∪ shared_core, typically 5-15 values) — well under the
     30-distractor cliff. Failed sections degrade gracefully into prose-only
     placeholders rather than killing the whole chapter.
  D. Merge — concatenate Section drafts into ChapterOutput; downstream
     audit / grader / curator / critic / assembler are unchanged.

Design choices:
  - Single file (not 3 fragmented modules) — keeps the new code surface
    auditable. Each phase is a small async function; the orchestrator
    `synthesize_hierarchical()` calls them in sequence.
  - Reuse existing primitives: `_invoke_structured_with_fallback` for LLM
    calls, `embed_texts` for embeddings, `_vault_bare_hashes` for hash
    extraction. No new infra.
  - No per-section Self-Refine in this v1 — section budgets get one shot
    each. The chapter-level outer Self-Refine loop (in distiller.py) still
    iterates if the merged audit fails; on retry, the whole hierarchical
    pipeline runs again with adjustment context. Future v2 can cache
    outline + routing across iterations.

References:
  - Chroma "Context Rot" (2024) — distractors degrade reliability monotonically
  - SurveyG (arxiv 2510.07733) — multi-agent hierarchical synthesis
  - LongRefiner (arxiv 2505.10413) — hierarchical refinement for long-context
  - Brenndoerfer — constrained-decoding prose/constraint preference-ratio
"""

from __future__ import annotations

import asyncio
import logging
import math
import re

from schemas.knowledge.agents import (
    ChapterOutline,
    ChapterOutput,
    ChapterPlan,
    OutlineSection,
    Section,
)
from schemas.knowledge.prompts import OUTLINE_PROMPT, SECTION_SYNTH_PROMPT
from services.knowledge.embeddings import embed_texts

from .helpers import _invoke_structured_with_fallback, _vault_bare_hashes

logger = logging.getLogger(__name__)


# Hierarchical synth activates when vault size exceeds this threshold.
# Below this, monolithic Self-Refine works reliably (per Run-13/Run-20
# small-chapter evidence: ch02 of Run-20 had vault=11 and ACCEPTed).
HIERARCHICAL_VAULT_THRESHOLD = 50

# Phase B params.
SHARED_CORE_K = 5            # top-K highest-entropy hashes go in every section
ORPHAN_RATE_CAP = 0.05       # >5% orphans → fall back to flat synth
ORPHAN_SIMILARITY_FLOOR = 0.20  # cosine below this = orphan
# Phase B context extraction window: chars to look back for the most recent
# heading anchoring this hash's source location.
_CONTEXT_LOOKBACK_CHARS = 600

# Phase A guardrails (deterministic critic).
_OUTLINE_MIN_SECTIONS = 4
_OUTLINE_MAX_SECTIONS = 15

# Phase C cap. asyncio.gather is unbounded; cap concurrency to avoid
# thundering-herd on the LiteLLM router (which has its own per-deployment
# rate limits but we burn cooldown TTL faster if we slam it).
# Batch 3 tuning (2026-05-14): bumped 6→8 to leverage the new
# per-provider semaphores in helpers.py (NIM=4) for within-chapter
# throughput. The actual concurrency is min(_PHASE_C_CONCURRENCY,
# provider_semaphore_cap × n_active_providers).
_PHASE_C_CONCURRENCY = 8


class HierarchicalSynthFailed(RuntimeError):
    """Raised when any phase fails irrecoverably; caller falls back to flat synth."""


# =============================================================================
# Phase A — outline
# =============================================================================
async def generate_outline(
    *,
    chapter: ChapterPlan,
    files_content: str,
    code_vault: dict[str, str],
    framework: str,
    tone_block: str,
    llm,
    iteration: int = 0,
    study_id: str | None = None,
    user_id: str | None = None,
) -> ChapterOutline:
    """Phase A: prose-only LLM call producing ChapterOutline."""
    # Phase 3.1 (2026-05-13): KD_USE_CLASSICAL_OUTLINE=1 routes through
    # services/knowledge/outline_classical.py — deterministic header-based
    # section extraction (zero LLM for naming) + 1 small LLM call for
    # the creative challenges+flashcards artifacts. ~80% token reduction,
    # same ChapterOutline shape so Phase B (vault routing), Phase C
    # (per-section synth), and Phase D (assemble) work unchanged. Default
    # "0" keeps the legacy single-large-LLM-call path until A/B validation
    # via /api/v1/knowledge/debug/outline_compare. See
    # docs/KD-SYNTH-LLM-TO-CLASSICAL-MAY2026.md Phase 3.
    import os as _os
    if _os.environ.get("KD_USE_CLASSICAL_OUTLINE", "0").strip().lower() in (
        "1", "true", "yes",
    ):
        from services.knowledge.outline_classical import generate_outline_classically
        return await generate_outline_classically(
            chapter=chapter,
            files_content=files_content,
            code_vault=code_vault,
            framework=framework,
            tone_block=tone_block,
            llm=llm,
            iteration=iteration,
            study_id=study_id,
            user_id=user_id,
        )
    label = f"hierarchical-outline-ch{chapter.number:02d}"
    return await _invoke_structured_with_fallback(
        prompt = OUTLINE_PROMPT,
        llm = llm,
        schema = ChapterOutline,
        invoke_vars = {
            "framework": framework,
            "chapter_number": str(chapter.number),
            "chapter_title": chapter.title,
            "chapter_goal": chapter.goal,
            "n_vault_hashes": str(len(code_vault)),
            "n_assigned_files": str(len(chapter.assigned_files)),
            "assigned_files_content": files_content,
            "tone_block": tone_block,
        },
        label = label,
        langfuse_session_id = study_id,
        langfuse_user_id = user_id,
        langfuse_tags = [
            f"ch{chapter.number:02d}",
            "hierarchical",
            "phase-a-outline",
            f"iter{iteration}",
        ],
        langfuse_run_name = (
            f"kd-hierarchical-outline-ch{chapter.number:02d}-iter{iteration}"
        ),
    )


def validate_outline(outline: ChapterOutline) -> tuple[bool, str]:
    """Cheap deterministic critic on the outline. Returns (ok, reason)."""
    if len(outline.sections) < _OUTLINE_MIN_SECTIONS:
        return False, (
            f"only {len(outline.sections)} sections "
            f"(need ≥ {_OUTLINE_MIN_SECTIONS})"
        )
    if len(outline.sections) > _OUTLINE_MAX_SECTIONS:
        return False, (
            f"{len(outline.sections)} sections "
            f"(max {_OUTLINE_MAX_SECTIONS})"
        )
    headings_lower = [s.heading.strip().lower() for s in outline.sections]
    if len(set(headings_lower)) != len(headings_lower):
        return False, "duplicate section headings"
    banned = {"introduction", "overview", "summary", "conclusion"}
    if any(h in banned for h in headings_lower):
        return False, f"banned heading present (one of {sorted(banned)})"
    for i, s in enumerate(outline.sections):
        if not s.goal or len(s.goal.strip()) < 10:
            return False, f"section {i} has empty/too-short goal"
    return True, "ok"


# =============================================================================
# Phase B — deterministic hash routing
# =============================================================================
class HashRouting:
    """Output of Phase B: per-section hash assignment + shared core + orphan rate.

    `hash_keys` + `hash_vecs` carry the embeddings forward to Phase A.5
    (split_overloaded_sections) so it can re-cluster within an overloaded
    section without re-embedding. Optional for backwards compat — callers
    that don't pass them just skip Phase A.5 (cluster fallback would re-
    embed via embed_texts if needed).
    """

    def __init__(
        self,
        section_hashes: dict[int, list[str]],
        shared_core: list[str],
        orphan_rate: float,
        n_orphans: int,
        n_total: int,
        hash_keys: list[str] | None = None,
        hash_vecs: list[list[float]] | None = None,
    ):
        self.section_hashes = section_hashes
        self.shared_core = shared_core
        self.orphan_rate = orphan_rate
        self.n_orphans = n_orphans
        self.n_total = n_total
        self.hash_keys = hash_keys or []
        self.hash_vecs = hash_vecs or []


def _extract_hash_contexts(
    files_content: str,
    code_vault: dict[str, str],
) -> dict[str, str]:
    """
    For each vault sentinel, build a short text capturing its local topical
    signal: the most recent preceding markdown heading + the first
    non-blank line of the code body. Used as the embedding target for
    Phase B routing.
    """
    contexts: dict[str, str] = {}
    for sentinel, fence_text in code_vault.items():
        idx = files_content.find(sentinel)
        last_heading = ""
        if idx != -1:
            prefix = files_content[max(0, idx - _CONTEXT_LOOKBACK_CHARS):idx]
            headings = re.findall(r"(?m)^#+\s+(.+)$", prefix)
            if headings:
                last_heading = headings[-1].strip()
        # First non-blank, non-fence line of the code body.
        code_signature = ""
        for line in fence_text.split("\n")[1:]:  # skip ```lang opener
            stripped = line.strip()
            if not stripped or stripped.startswith("```") or stripped.startswith("~~~"):
                continue
            code_signature = stripped[:120]
            break
        # Combine heading + code signature; if both empty, fall back to
        # first 200 chars of the fence body.
        combined = f"{last_heading}\n{code_signature}".strip()
        if not combined:
            combined = fence_text[:200]
        contexts[sentinel] = combined
    return contexts


def _cosine_similarity_matrix(
    A: list[list[float]],
    B: list[list[float]],
) -> list[list[float]]:
    """Pure-Python cosine similarity matrix |A| × |B|. Avoids numpy dep here."""
    def _dot(u, v):
        return sum(x * y for x, y in zip(u, v))

    def _norm(u):
        return math.sqrt(sum(x * x for x in u))

    norms_a = [_norm(a) for a in A]
    norms_b = [_norm(b) for b in B]
    sims: list[list[float]] = []
    for i, a in enumerate(A):
        row: list[float] = []
        na = norms_a[i] or 1.0
        for j, b in enumerate(B):
            nb = norms_b[j] or 1.0
            row.append(_dot(a, b) / (na * nb))
        sims.append(row)
    return sims


def _row_entropy(row: list[float]) -> float:
    """Shannon entropy of a similarity row (after softmax-like normalization).

    High entropy = hash is roughly equally relevant to all sections =
    candidate for shared_core. Negative similarities are clamped to 0.
    """
    clipped = [max(0.0, x) for x in row]
    total = sum(clipped) or 1.0
    p = [x / total for x in clipped]
    return -sum(pi * math.log(pi + 1e-12) for pi in p if pi > 0)


async def route_hashes_to_sections(
    *,
    outline: ChapterOutline,
    code_vault: dict[str, str],
    files_content: str,
) -> HashRouting:
    """
    Phase B: deterministic. Embed (heading + goal) per section, embed
    (preceding heading + code signature) per vault hash, compute cosine
    similarity, assign each hash to argmax section. Identify shared_core
    by entropy. Compute orphan rate (max-similarity < ORPHAN_SIMILARITY_FLOOR).
    """
    n_sections = len(outline.sections)
    if n_sections == 0:
        raise HierarchicalSynthFailed("outline has zero sections (validate_outline missed it)")

    section_texts = [
        f"{s.heading}: {s.goal}" for s in outline.sections
    ]
    section_vecs, _ = await embed_texts(section_texts)

    contexts = _extract_hash_contexts(files_content, code_vault)
    hash_keys = list(code_vault.keys())
    hash_texts = [contexts[k] for k in hash_keys]
    hash_vecs, _ = await embed_texts(hash_texts)

    sims = _cosine_similarity_matrix(hash_vecs, section_vecs)

    # Identify shared_core: top-K highest-entropy hashes.
    entropies = [_row_entropy(row) for row in sims]
    sorted_indices = sorted(range(len(hash_keys)), key=lambda i: entropies[i], reverse=True)
    shared_core_indices: set[int] = set(
        sorted_indices[:min(SHARED_CORE_K, max(0, len(hash_keys) - n_sections))]
    )
    shared_core = [hash_keys[i] for i in shared_core_indices]

    # Assign remaining hashes.
    section_hashes: dict[int, list[str]] = {i: [] for i in range(n_sections)}
    n_orphans = 0
    for h_idx, key in enumerate(hash_keys):
        if h_idx in shared_core_indices:
            continue
        row = sims[h_idx]
        max_sim = max(row) if row else 0.0
        if max_sim < ORPHAN_SIMILARITY_FLOOR:
            n_orphans += 1
        sec_idx = max(range(n_sections), key=lambda j: row[j])
        section_hashes[sec_idx].append(key)

    n_total = len(hash_keys)
    orphan_rate = (n_orphans / n_total) if n_total else 0.0

    # Best-effort balance: if any section ended up with zero non-shared hashes,
    # nudge the closest under-utilized hash to it. This avoids per-section
    # synth calls with ONLY shared-core (which makes the section feel
    # accidental rather than topical).
    for sec_idx in range(n_sections):
        if section_hashes[sec_idx]:
            continue
        # Find the hash with the highest similarity to this section among
        # all non-shared hashes currently routed elsewhere.
        candidates: list[tuple[float, int, int]] = []  # (sim, h_idx, current_sec)
        for h_idx, key in enumerate(hash_keys):
            if h_idx in shared_core_indices:
                continue
            sim_to_target = sims[h_idx][sec_idx]
            current_sec = max(
                range(n_sections),
                key=lambda j: sims[h_idx][j],
            )
            candidates.append((sim_to_target, h_idx, current_sec))
        if not candidates:
            continue
        candidates.sort(reverse=True)  # highest sim_to_target first
        sim, h_idx, current_sec = candidates[0]
        # Move this hash from its current section to the empty one.
        if current_sec != sec_idx:
            try:
                section_hashes[current_sec].remove(hash_keys[h_idx])
            except ValueError:
                pass
            section_hashes[sec_idx].append(hash_keys[h_idx])

    # Fix 3 v1 REVERTED 2026-05-12 night-late after research validation —
    # cross-topic redistribution destroys natural topic coherence when source
    # material has skewed distribution (e.g., 60% topic A → flat cap moves
    # topic A's hashes into UNRELATED sections). See KD-SYNTH-LLM-TO-CLASSICAL-
    # MAY2026.md "Phase B/C audit-fail hardening — Fix 3 v2".
    #
    # The proper fix is Phase A.5 bucket-split (in `split_overloaded_sections`
    # below) which preserves topic coherence by splitting an overloaded section
    # INTO ITS OWN SUB-SECTIONS (same parent heading, sibling sub-headings)
    # rather than dispersing hashes to other topics. Pattern source:
    # GraphRAG (Edge et al., arXiv 2404.16130) hierarchical community split;
    # STORM (Shao et al., NAACL 2024) outline expansion under heavy topics.

    return HashRouting(
        section_hashes = section_hashes,
        shared_core = shared_core,
        orphan_rate = orphan_rate,
        n_orphans = n_orphans,
        n_total = n_total,
        hash_keys = hash_keys,
        hash_vecs = hash_vecs,
    )


def _bare_hash_from_sentinel(sentinel: str) -> str:
    """Extract the 12-hex bare hash from a `<code-ref hash="..."/>` sentinel."""
    m = re.search(r'hash="([a-f0-9]{12})"', sentinel)
    return m.group(1) if m else ""


# =============================================================================
# Phase A.5 — adaptive sub-section split for overloaded sections (2026-05-12 night-late)
# =============================================================================
# Pattern source: GraphRAG hierarchical community split (Edge et al., arXiv
# 2404.16130) + STORM outline expansion (Shao et al., NAACL 2024) +
# LLM×MapReduce tree-oriented map-reduce (THUNLP, ACL 2025 Long, arXiv
# 2410.09342). All three handle skewed topic distribution by SPLITTING the
# heavy topic into sub-sections under the same parent — never by
# redistributing across unrelated topics.
#
# Empirical justification: study 64b1cf9a (FastAPI, 2026-05-12) showed
# ch04's Phase B routing assigned 32 of 53 hashes to a single section.
# Asking the LLM section-synth to faithfully list 32 specific 12-hex
# hashes in one structured-output array exceeds reliable recall on every
# May-2026 frontier model we tested (Kimi K2.6, GLM-5.1, MiniMax M2.7,
# DeepSeek V4-Flash all dropped ≥10% of hashes). JSONSchemaBench (ICLR
# 2025, arXiv 2501.10868) confirms: constrained-decoding holds shape but
# degrades recall as enumeration cardinality grows.
#
# Pattern: when a section has >MAX_HASHES_PER_SECTION hashes, k-means
# cluster its hash embeddings into k = ceil(n / MAX) sub-buckets, create
# k sub-sections under the same parent heading ("<parent> — Part i of k"),
# each sub-section inherits the parent's `goal` and `assumes_from_prior_
# sections`, and the original hashes route to whichever sub-section their
# cluster centroid is closest to. Result: topic coherence preserved,
# every Phase C LLM call sees ≤MAX hashes, audit gate clears naturally.
MAX_HASHES_PER_SECTION_BUCKET = 10


def split_overloaded_sections(
    outline: ChapterOutline,
    routing: HashRouting,
    max_per_section: int = MAX_HASHES_PER_SECTION_BUCKET,
) -> tuple[ChapterOutline, HashRouting]:
    """
    Phase A.5: bucket-split overloaded sections under same parent heading.

    For each section in `outline.sections` whose routed hash count exceeds
    `max_per_section`, split into k = ceil(n / max_per_section) sub-sections.
    Sub-sections share the parent's heading prefix + the parent's goal +
    assumes_from_prior_sections; only the heading differs ("— Part i of k").
    Hash assignment within the parent's set uses k-means on the hash
    embeddings (carried forward from Phase B's HashRouting).

    Hard caps:
      - ChapterOutline allows 4-15 sections; if expansion exceeds 15 we cap
        by merging the smallest siblings within the same parent (rare; only
        triggers for chapters with 150+ hashes).
      - Falls back to no-op split if hash_vecs are missing (backwards compat
        for callers that don't pass embeddings forward).

    Returns (new_outline, new_routing). Both downstream-compatible with
    Phase C — no signature changes to synthesize_one_section.
    """
    import math
    n_orig = len(outline.sections)

    # No-op path: nothing to split if no section exceeds the cap.
    overloaded = [
        i for i in range(n_orig)
        if len(routing.section_hashes.get(i, [])) > max_per_section
    ]
    if not overloaded:
        return outline, routing

    # Cluster fallback: if embeddings weren't carried forward, split
    # chronologically (preserves narrative flow within the topic). This
    # is the safe fallback shape — not as semantically tight as k-means
    # but still under cap and topically coherent.
    use_kmeans = bool(routing.hash_keys and routing.hash_vecs)
    if use_kmeans:
        try:
            import numpy as np
            from sklearn.cluster import KMeans
        except Exception:
            use_kmeans = False

    hash_to_idx = (
        {h: i for i, h in enumerate(routing.hash_keys)}
        if use_kmeans else {}
    )

    new_sections: list[OutlineSection] = []
    new_section_hashes: dict[int, list[str]] = {}
    split_log: list[str] = []

    for orig_idx, section in enumerate(outline.sections):
        section_hash_list = routing.section_hashes.get(orig_idx, [])
        n = len(section_hash_list)

        if n <= max_per_section:
            new_idx = len(new_sections)
            new_sections.append(section)
            new_section_hashes[new_idx] = section_hash_list
            continue

        # Need to split: k = ceil(n / max).
        k = math.ceil(n / max_per_section)

        if use_kmeans and len(section_hash_list) >= k:
            # K-means cluster within the parent's hash set.
            try:
                section_vecs = np.array([
                    routing.hash_vecs[hash_to_idx[h]] for h in section_hash_list
                ])
                km = KMeans(
                    n_clusters=k, n_init=3, random_state=42,
                ).fit(section_vecs)
                labels = km.labels_.tolist()
            except Exception:
                # Cluster fallback: equal-size chronological chunks.
                labels = [i % k for i in range(n)]
        else:
            # No embeddings: chronological chunks (preserves document order).
            chunk_size = math.ceil(n / k)
            labels = [i // chunk_size for i in range(n)]

        clusters: dict[int, list[str]] = {i: [] for i in range(k)}
        for h, lab in zip(section_hash_list, labels):
            clusters[lab].append(h)

        # Emit sub-sections in cluster order; skip empty clusters.
        actual_subs = sum(1 for c in clusters.values() if c)
        sub_count = 0
        for cluster_idx in range(k):
            cluster_hashes = clusters[cluster_idx]
            if not cluster_hashes:
                continue
            sub_count += 1
            sub_heading = (
                f"{section.heading} — Part {sub_count} of {actual_subs}"
            )
            new_idx = len(new_sections)
            new_sections.append(OutlineSection(
                heading=sub_heading,
                goal=section.goal,
                assumes_from_prior_sections=section.assumes_from_prior_sections,
            ))
            new_section_hashes[new_idx] = cluster_hashes
        split_log.append(
            f"'{section.heading}' ({n} hashes → {actual_subs} sub-sections)"
        )

    # Hard cap at ChapterOutline.sections.max_length (40 since 2026-05-12
    # night, Fix #1 of Phase B/C audit-fail hardening — was 15 before).
    # 40 supports up to 400-hash chapters cleanly at 10 hashes/section.
    # Triggers only for very dense outliers (rare).
    _SCHEMA_MAX_SECTIONS = 40
    if len(new_sections) > _SCHEMA_MAX_SECTIONS:
        logger.warning(
            f"[bucket-split] expanded to {len(new_sections)} sections, "
            f"exceeds ChapterOutline max={_SCHEMA_MAX_SECTIONS}; capping by "
            f"merging trailing sub-sections into the last in-budget section "
            f"(consider raising max or splitting chapter at planner level)"
        )
        # Merge overflow into the last fittable section.
        _keep_n = _SCHEMA_MAX_SECTIONS - 1  # leave 1 slot for merged tail
        keep = new_sections[:_keep_n]
        merged_hashes: list[str] = []
        for i in range(_keep_n, len(new_sections)):
            merged_hashes.extend(new_section_hashes[i])
        tail_heading = "Additional"
        keep.append(OutlineSection(
            heading=tail_heading,
            goal=(new_sections[_keep_n].goal
                  if len(new_sections) > _keep_n
                  else "Additional related content."),
            assumes_from_prior_sections="",
        ))
        new_section_hashes = {
            i: new_section_hashes[i] for i in range(_keep_n)
        }
        new_section_hashes[_keep_n] = merged_hashes
        new_sections = keep

    if split_log:
        logger.info(
            f"[bucket-split] split {len(split_log)} overloaded "
            f"section(s): {'; '.join(split_log)} "
            f"(orig {n_orig} → new {len(new_sections)})"
        )

    new_outline = ChapterOutline(
        sections=new_sections,
        challenges=outline.challenges,
        flashcards=outline.flashcards,
    )
    new_routing = HashRouting(
        section_hashes=new_section_hashes,
        shared_core=routing.shared_core,
        orphan_rate=routing.orphan_rate,
        n_orphans=routing.n_orphans,
        n_total=routing.n_total,
        hash_keys=routing.hash_keys,
        hash_vecs=routing.hash_vecs,
    )
    return new_outline, new_routing


# =============================================================================
# Phase C — per-section parallel synth
# =============================================================================
async def synthesize_one_section(
    *,
    outline_section: OutlineSection,
    section_index: int,
    n_sections: int,
    assigned_hashes: list[str],
    shared_core: list[str],
    files_content: str,
    framework: str,
    tone_block: str,
    chapter: ChapterPlan,
    llm,
    iteration: int = 0,
    study_id: str | None = None,
    user_id: str | None = None,
) -> Section:
    """Phase C: synthesize ONE section. Returns Section (heading + prose + code_refs)."""
    valid_sentinels = list(set(assigned_hashes) | set(shared_core))
    valid_bare = sorted({_bare_hash_from_sentinel(s) for s in valid_sentinels} - {""})
    if not valid_bare:
        # No hashes at all routed here AND no shared_core — emit a
        # placeholder Section (orientation-only) rather than crash.
        # This usually only happens for tiny chapters where SHARED_CORE_K
        # was capped down to 0.
        logger.warning(
            f"[hierarchical][ch{chapter.number:02d}][sec{section_index}] "
            f"empty whitelist — emitting orientation-only placeholder Section"
        )
        return Section(
            heading = outline_section.heading,
            prose_md = (
                f"_This section anchors the reader between adjacent topics. "
                f"{outline_section.goal}_"
            ),
            code_refs = [],
        )

    orientation_clause = (
        "This is the FIRST section — open with 2-3 sentences of "
        "ORIENTATION before any code: what the reader will learn in "
        "this section, why it matters, what prerequisites are assumed."
        if section_index == 0
        else (
            "Open with a 1-sentence bridge from the prior section, then "
            "dive into the section's content. Do NOT re-introduce the "
            "chapter — the reader has already entered."
        )
    )

    label = (
        f"hierarchical-section-ch{chapter.number:02d}-sec{section_index:02d}"
    )

    try:
        result = await _invoke_structured_with_fallback(
            prompt = SECTION_SYNTH_PROMPT,
            llm = llm,
            schema = Section,
            invoke_vars = {
                "framework": framework,
                "chapter_number": str(chapter.number),
                "chapter_title": chapter.title,
                "section_heading": outline_section.heading,
                "section_goal": outline_section.goal,
                "assumes_from_prior_sections": (
                    outline_section.assumes_from_prior_sections or "(none — first section)"
                ),
                "valid_hashes_csv": ", ".join(valid_bare),
                "assigned_files_content": files_content,
                "tone_block": tone_block,
                "orientation_clause": orientation_clause,
            },
            label = label,
            langfuse_session_id = study_id,
            langfuse_user_id = user_id,
            langfuse_tags = [
                f"ch{chapter.number:02d}",
                "hierarchical",
                "phase-c-section",
                f"sec{section_index:02d}",
                f"iter{iteration}",
            ],
            langfuse_run_name = (
                f"kd-hierarchical-section-ch{chapter.number:02d}"
                f"-sec{section_index:02d}-iter{iteration}"
            ),
        )
    except Exception as e:
        logger.warning(
            f"[hierarchical][ch{chapter.number:02d}][sec{section_index}] "
            f"section synth failed ({type(e).__name__}: {e}) — "
            f"emitting placeholder Section to keep chapter assembleable"
        )
        return Section(
            heading = outline_section.heading,
            prose_md = (
                f"_Section synthesis failed; reader sees the assigned vault "
                f"hashes below for reference. Goal: {outline_section.goal}_"
            ),
            code_refs = valid_bare,
        )

    # Defensive coerce: filter code_refs to whitelist (model may still
    # invent despite the prompt). Phase D's audit will detect the loss
    # and the chapter-level Self-Refine can retry.
    if result.code_refs:
        valid_set = set(valid_bare)
        clean_refs = [r for r in result.code_refs if r in valid_set]
        if len(clean_refs) != len(result.code_refs):
            logger.info(
                f"[hierarchical][ch{chapter.number:02d}][sec{section_index}] "
                f"filtered {len(result.code_refs) - len(clean_refs)} "
                f"out-of-whitelist code_refs"
            )
            result = Section(
                heading = result.heading,
                prose_md = result.prose_md,
                code_refs = clean_refs,
            )

    return result


# =============================================================================
# Top-level orchestrator
# =============================================================================
async def synthesize_hierarchical(
    *,
    chapter: ChapterPlan,
    files_content: str,
    code_vault: dict[str, str],
    framework: str,
    tone_block: str,
    previous_adjustments: list[str],
    llm,
    iteration: int = 0,
    study_id: str | None = None,
    user_id: str | None = None,
    prior_chapter_output: ChapterOutput | None = None,
) -> ChapterOutput:
    """
    4-phase hierarchical synthesis. Returns a ChapterOutput identical in
    shape to monolithic `_synthesize_attempt` so downstream audit / grader /
    curator / critic / assembler don't change.

    Raises HierarchicalSynthFailed on irrecoverable phase failure (caller
    falls back to flat synth).
    """
    n_vault = len(code_vault)
    logger.info(
        f"[hierarchical][ch{chapter.number:02d}] starting (vault={n_vault} hashes, "
        f"iter={iteration})"
    )

    # Phase A — outline
    try:
        outline = await generate_outline(
            chapter = chapter,
            files_content = files_content,
            code_vault = code_vault,
            framework = framework,
            tone_block = tone_block,
            llm = llm,
            iteration = iteration,
            study_id = study_id,
            user_id = user_id,
        )
    except Exception as e:
        raise HierarchicalSynthFailed(
            f"Phase A outline generation failed: {type(e).__name__}: {e}"
        ) from e

    ok, reason = validate_outline(outline)
    if not ok:
        raise HierarchicalSynthFailed(f"Phase A outline rejected: {reason}")

    n_sections = len(outline.sections)
    logger.info(
        f"[hierarchical][ch{chapter.number:02d}] Phase A: "
        f"{n_sections} sections — "
        f"{', '.join(s.heading[:40] for s in outline.sections[:5])}"
        f"{'...' if n_sections > 5 else ''}"
    )

    # Phase B — deterministic routing
    try:
        routing = await route_hashes_to_sections(
            outline = outline,
            code_vault = code_vault,
            files_content = files_content,
        )
    except Exception as e:
        raise HierarchicalSynthFailed(
            f"Phase B hash routing failed: {type(e).__name__}: {e}"
        ) from e

    logger.info(
        f"[hierarchical][ch{chapter.number:02d}] Phase B: "
        f"{n_vault} hashes → {n_sections} sections "
        f"(shared_core={len(routing.shared_core)}, "
        f"orphan_rate={routing.orphan_rate:.1%}, "
        f"per-section sizes="
        f"{[len(routing.section_hashes[i]) for i in range(n_sections)]})"
    )

    if routing.orphan_rate > ORPHAN_RATE_CAP:
        raise HierarchicalSynthFailed(
            f"Phase B orphan_rate {routing.orphan_rate:.1%} > cap "
            f"{ORPHAN_RATE_CAP:.1%} ({routing.n_orphans}/{routing.n_total} "
            f"hashes can't fit any section)"
        )

    # Phase A.5 — bucket-split overloaded sections (2026-05-12 night-late)
    # See split_overloaded_sections docstring + KD-SYNTH-LLM-TO-CLASSICAL-
    # MAY2026.md Phase B/C audit-fail hardening § Fix 3 v2.
    try:
        outline, routing = split_overloaded_sections(
            outline=outline,
            routing=routing,
            max_per_section=MAX_HASHES_PER_SECTION_BUCKET,
        )
        if len(outline.sections) != n_sections:
            n_sections = len(outline.sections)
            logger.info(
                f"[hierarchical][ch{chapter.number:02d}] Phase A.5: "
                f"bucket-split expanded to {n_sections} sections "
                f"(per-section sizes="
                f"{[len(routing.section_hashes[i]) for i in range(n_sections)]})"
            )
    except Exception as e:
        logger.warning(
            f"[hierarchical][ch{chapter.number:02d}] Phase A.5 split failed "
            f"({type(e).__name__}: {e}); continuing with original routing"
        )

    # Phase C — parallel per-section synth (capped concurrency)
    # Batch 4 speed fix (2026-05-14): per-section cache across refine iters.
    # Canary v7 ch02 evidence: iter 0 produced 41/47 missing hashes; iter 1
    # produced 0 missing but Phase C re-ran ALL 12 sections from scratch
    # including the ~6 sections that were already legitimate prose. With
    # per-section reuse, only the sections that LOOK broken at the end of
    # the prior iter get re-synthesized.
    #
    # Reuse heuristic (conservative — only carries forward sections that
    # already look fine to avoid amplifying audit failures):
    #   - same outline shape (n_sections matches prior)
    #   - same heading text at this index (Phase A outline LLM might vary)
    #   - prior prose_md ≥ 600 chars (filters thin sections)
    #   - prior code_refs ≥ 1 (filters empty-citation sections)
    # Audit defects (missing/invented/fence/duplicated) all violate one of
    # these → those sections are NOT carried, get fresh synthesis.
    semaphore = asyncio.Semaphore(_PHASE_C_CONCURRENCY)

    prior_sections_by_idx: dict[int, Section] = {}
    if (
        prior_chapter_output is not None
        and iteration > 0
        and len(prior_chapter_output.sections) == n_sections
    ):
        for _i, _prior in enumerate(prior_chapter_output.sections):
            _prior_heading = (getattr(_prior, "heading", "") or "").strip()
            _curr_heading = (outline.sections[_i].heading or "").strip()
            if _prior_heading != _curr_heading:
                continue
            _prose_len = len(_prior.prose_md or "")
            _ref_count = len(_prior.code_refs or [])
            if _prose_len >= 600 and _ref_count >= 1:
                prior_sections_by_idx[_i] = _prior
        if prior_sections_by_idx:
            logger.info(
                f"[hierarchical][ch{chapter.number:02d}] iter {iteration}: "
                f"reusing {len(prior_sections_by_idx)}/{n_sections} sections "
                f"from prior iter (saved Phase C LLM calls)"
            )

    async def _bounded(i: int) -> Section:
        if i in prior_sections_by_idx:
            return prior_sections_by_idx[i]
        async with semaphore:
            return await synthesize_one_section(
                outline_section = outline.sections[i],
                section_index = i,
                n_sections = n_sections,
                assigned_hashes = routing.section_hashes[i],
                shared_core = routing.shared_core,
                files_content = files_content,
                framework = framework,
                tone_block = tone_block,
                chapter = chapter,
                llm = llm,
                iteration = iteration,
                study_id = study_id,
                user_id = user_id,
            )

    # Use return_exceptions=True so a single bad section doesn't kill the
    # whole gather — synthesize_one_section already swallows failures and
    # returns placeholder Sections, but defensive belt + suspenders.
    drafts = await asyncio.gather(
        *(_bounded(i) for i in range(n_sections)),
        return_exceptions = True,
    )

    final_sections: list[Section] = []
    failed_count = 0
    for i, draft in enumerate(drafts):
        if isinstance(draft, BaseException):
            failed_count += 1
            logger.warning(
                f"[hierarchical][ch{chapter.number:02d}] section {i} "
                f"raised through gather: {type(draft).__name__}: {draft}"
            )
            # Same shape as synthesize_one_section's internal fallback.
            valid_sentinels = list(
                set(routing.section_hashes[i]) | set(routing.shared_core)
            )
            valid_bare = sorted({
                _bare_hash_from_sentinel(s) for s in valid_sentinels
            } - {""})
            final_sections.append(Section(
                heading = outline.sections[i].heading,
                prose_md = (
                    f"_Section synthesis raised an exception; placeholder "
                    f"emitted to preserve chapter structure. Goal: "
                    f"{outline.sections[i].goal}_"
                ),
                code_refs = valid_bare,
            ))
        else:
            final_sections.append(draft)

    # Hard fail if more than half of sections crashed — better to let the
    # caller fall back to flat synth than ship a hollow chapter.
    if failed_count > n_sections // 2:
        raise HierarchicalSynthFailed(
            f"Phase C: {failed_count}/{n_sections} sections failed — "
            f"too many to deliver a coherent chapter"
        )

    logger.info(
        f"[hierarchical][ch{chapter.number:02d}] Phase C complete: "
        f"{n_sections} sections synthesized "
        f"({failed_count} placeholder, {n_sections - failed_count} real)"
    )

    # Phase D — merge into ChapterOutput. challenges + flashcards come
    # from the outline LLM call (holistic over the whole chapter source).
    return ChapterOutput(
        sections = final_sections,
        challenges = outline.challenges,
        flashcards = outline.flashcards,
    )
