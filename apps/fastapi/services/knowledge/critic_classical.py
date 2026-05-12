"""
Knowledge Distiller — Classical (Deterministic) Critic (Phase 2.1, 2026-05-13)

Replaces the post-synth critic's LLM call (`CRITIC_PROMPT` /
`_critic_one_chapter` per-chapter parallel in distiller.py lines 2092-2153)
with a deterministic + embedding-similarity hybrid. Returns the same
`CriticAssessment` shape so the assembler / DEBT.md / validation_report.json
contracts work unchanged.

Background:
- `citation_coverage` is ALREADY deterministic in the critic node
  (distiller.py line 2067 — `_scan_citations` + ratio).
- `code_syntax_valid` is ALREADY deterministic via tree-sitter
  (`_compute_code_syntax_valid_score`, OP-59 2026-04-25). It OVERRIDES
  the LLM's score.
- `faithfulness` was the only dim still requiring an LLM call.

This module's `score_faithfulness_classical` replaces the LLM faithfulness
judgment with an embedding-similarity heuristic:
1. Find every sentence containing a `# docs: <slug>` citation.
2. For each, embed (sentence, source_content) via the existing kd-embed
   rotator and compute cosine similarity.
3. Aggregate to a per-chapter score in [0, 1].

Not as accurate as MiniCheck-7B / AlignScore-large (the May 2026 SoTA
NLI faithfulness models per the synth doc), but zero new infra and
respects the no-in-cluster-inference rule per
`feedback_local_vs_rotator_architecture` memory. Phase 2.2 can upgrade to
a host-side NLI model when/if the heuristic proves insufficient.

Why embedding similarity is a reasonable proxy:
- A claim grounded in its cited source will have high embedding overlap
  (semantic congruence) with that source.
- A hallucinated claim will have low overlap (the source text doesn't
  mention what's being claimed).
- Cosine threshold around 0.5-0.6 separates "this claim is grounded" from
  "this claim is invented" on tight technical-doc corpora; lower for
  paraphrased/abstractive claims. We calibrate via experiment.
"""
from __future__ import annotations

import logging
import re

import numpy as np

from schemas.knowledge.agents import CriticAssessment
from services.knowledge.embeddings import embed_texts


logger = logging.getLogger(__name__)


# =============================================================================
# Tuning constants
# =============================================================================
# Map raw cosine score [-1, 1] to faithfulness score [0, 1]:
#   cos >= _FAITH_HIGH  → faithfulness 1.0
#   cos <= _FAITH_LOW   → faithfulness 0.0
#   linear interpolation between
# 0.45 / 0.20 are conservative defaults; tune after side-by-side validation.
_FAITH_HIGH = 0.45
_FAITH_LOW = 0.20

# Max chars of source content to embed per citation. NIM nemotron-embed-1b-v2
# handles up to 8K context; we stay well under to keep latency tight.
_MAX_SOURCE_CHARS = 4000

# Max chars of citation context (preceding sentence) to embed.
_MAX_CITATION_CONTEXT_CHARS = 800

# Citation regex — `# docs: <slug>` (same shape as the grader's).
_CITATION_RE = re.compile(r"#\s*docs:\s*([\w/.\-]+)")

# Cap on citations checked per chapter to keep wall-clock bounded.
# Studies with >40 citations/chapter are rare; sampling is fine if hit.
_MAX_CITATIONS_PER_CHAPTER = 40


def _extract_citations_with_context(chapter_text: str) -> list[tuple[str, str]]:
    """
    Return list of `(slug, context)` tuples — one per `# docs:` citation
    found. `context` is the preceding sentence (or paragraph chunk) the
    citation is attached to. Used as the "claim" side of the
    claim-vs-source faithfulness comparison.
    """
    results: list[tuple[str, str]] = []
    for m in _CITATION_RE.finditer(chapter_text):
        slug = m.group(1)
        # Take the chunk preceding the citation, back to the last blank line
        # or up to _MAX_CITATION_CONTEXT_CHARS, whichever is closer.
        start = max(0, m.start() - _MAX_CITATION_CONTEXT_CHARS)
        chunk = chapter_text[start:m.start()].strip()
        # Trim to the last sentence boundary inside the chunk for cleaner
        # claim isolation. Falls back to the full chunk if no boundary.
        boundaries = [chunk.rfind(". "), chunk.rfind("?\n"), chunk.rfind("!\n")]
        last = max(boundaries)
        if last > 0:
            chunk = chunk[last + 2:].strip()
        if chunk:
            results.append((slug, chunk[:_MAX_CITATION_CONTEXT_CHARS]))
        if len(results) >= _MAX_CITATIONS_PER_CHAPTER:
            break
    return results


def _cos(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two 1-D vectors."""
    na = np.linalg.norm(a)
    nb = np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def _cos_to_faithfulness(cos: float) -> float:
    """Map raw cosine [-1, 1] → faithfulness score [0, 1] via clipped linear."""
    if cos >= _FAITH_HIGH:
        return 1.0
    if cos <= _FAITH_LOW:
        return 0.0
    return (cos - _FAITH_LOW) / (_FAITH_HIGH - _FAITH_LOW)


async def score_faithfulness_classical(
    chapter_text: str,
    source_contents: dict[str, str],
) -> tuple[float, list[str]]:
    """
    Score chapter faithfulness via embedding-similarity claim grounding.

    Args:
        chapter_text:    full chapter README markdown (assembled).
        source_contents: dict {slug → source_content} loaded by caller.
                         Slugs are the values seen in `# docs:` citations.

    Returns:
        (faithfulness_score, list_of_issues)
        faithfulness_score ∈ [0, 1] — mean per-citation grounding.
        issues — list of "ch?? cited 'X' but content is ungrounded (cos=Y)".
    """
    citations = _extract_citations_with_context(chapter_text)
    if not citations:
        # No citations → can't measure grounding. Return 0.5 (neutral)
        # rather than 1.0 (false positive of "everything is grounded").
        return 0.5, []

    # Batch embed both claims and sources in one go (cheaper than per-pair).
    # Order: [claim_0, claim_1, ..., source_0, source_1, ...]
    claims = [ctx for _, ctx in citations]
    sources_by_idx: list[str] = []
    valid_idx: list[int] = []  # indices in `citations` that have a source
    for i, (slug, _) in enumerate(citations):
        src = source_contents.get(slug)
        if src is None:
            # Try common slug normalization (lowercase, with/without .md)
            for k in source_contents.keys():
                if k.lower() == slug.lower() or k.lower() == slug.lower() + ".md":
                    src = source_contents[k]
                    break
        if src:
            sources_by_idx.append(src[:_MAX_SOURCE_CHARS])
            valid_idx.append(i)

    if not valid_idx:
        # All cited slugs missing from source_contents → 0.0 grounding.
        return 0.0, [
            f"all {len(citations)} cited slugs missing from research/raw/ — "
            f"chapter cites slugs that don't exist (likely hallucinated)"
        ]

    # Embed: claims for the valid_idx + their corresponding sources.
    valid_claims = [claims[i] for i in valid_idx]
    texts = valid_claims + sources_by_idx
    try:
        vectors, _ = await embed_texts(texts)
    except Exception as e:
        logger.warning(
            f"[critic-classical] embed_texts failed during faithfulness: "
            f"{type(e).__name__}: {e}; defaulting to 0.5"
        )
        return 0.5, [f"faithfulness embedding failed: {type(e).__name__}"]

    arr = np.asarray(vectors, dtype=np.float32)
    n_pairs = len(valid_idx)
    claim_vecs = arr[:n_pairs]
    source_vecs = arr[n_pairs:]

    per_citation_faith: list[float] = []
    issues: list[str] = []
    for k, idx in enumerate(valid_idx):
        slug, ctx = citations[idx]
        cos = _cos(claim_vecs[k], source_vecs[k])
        faith = _cos_to_faithfulness(cos)
        per_citation_faith.append(faith)
        if faith < 0.4:
            issues.append(
                f"weakly-grounded claim cited '{slug}' (cos={cos:.2f}, "
                f"faith={faith:.2f}): {ctx[:80]}..."
            )

    # Also count missing-source slugs as 0 grounding
    n_missing = len(citations) - len(valid_idx)
    if n_missing > 0:
        per_citation_faith.extend([0.0] * n_missing)
        issues.append(
            f"{n_missing} cited slug(s) absent from research/raw/ → scored 0.0"
        )

    score = float(np.mean(per_citation_faith))
    return score, issues


async def assess_chapter_classically(
    chapter_text: str,
    citation_coverage: float,
    code_syntax_valid: float,
    source_contents: dict[str, str],
) -> CriticAssessment:
    """
    Build a full `CriticAssessment` using classical scorers for all 3 dims:
        - citation_coverage: passed in (caller computes via _scan_citations)
        - code_syntax_valid: passed in (caller computes via _compute_*_score)
        - faithfulness:      embedding-similarity heuristic, this module

    `overall_score` follows the same weighted-composite pattern the LLM
    critic uses (per CRITIC_PROMPT lines 730-733):
        overall = 0.4 * citation_coverage + 0.4 * faithfulness + 0.2 * code_syntax_valid
    """
    faith, issues = await score_faithfulness_classical(
        chapter_text, source_contents,
    )
    # Same weighting as the current critic merge (post-OP-59-WEIGHT-DROP):
    # citation 0.4 + faithfulness 0.4 + code 0.2
    overall = (
        0.4 * citation_coverage
        + 0.4 * faith
        + 0.2 * code_syntax_valid
    )
    return CriticAssessment(
        citation_coverage=citation_coverage,
        faithfulness=faith,
        code_syntax_valid=code_syntax_valid,
        overall_score=overall,
        issues=issues,
    )
