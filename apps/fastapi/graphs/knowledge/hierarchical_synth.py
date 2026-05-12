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
_PHASE_C_CONCURRENCY = 6


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
    """Output of Phase B: per-section hash assignment + shared core + orphan rate."""

    def __init__(
        self,
        section_hashes: dict[int, list[str]],
        shared_core: list[str],
        orphan_rate: float,
        n_orphans: int,
        n_total: int,
    ):
        self.section_hashes = section_hashes
        self.shared_core = shared_core
        self.orphan_rate = orphan_rate
        self.n_orphans = n_orphans
        self.n_total = n_total


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

    return HashRouting(
        section_hashes = section_hashes,
        shared_core = shared_core,
        orphan_rate = orphan_rate,
        n_orphans = n_orphans,
        n_total = n_total,
    )


def _bare_hash_from_sentinel(sentinel: str) -> str:
    """Extract the 12-hex bare hash from a `<code-ref hash="..."/>` sentinel."""
    m = re.search(r'hash="([a-f0-9]{12})"', sentinel)
    return m.group(1) if m else ""


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

    # Phase C — parallel per-section synth (capped concurrency)
    semaphore = asyncio.Semaphore(_PHASE_C_CONCURRENCY)

    async def _bounded(i: int) -> Section:
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
