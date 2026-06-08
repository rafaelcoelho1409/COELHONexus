"""ycs/graph_builder — PURE entity-resolution helpers.

Functional Core (`docs/CODE-CONVENTIONS.md` §4): no Neo4j, no LLM, no
rapidfuzz import. The fuzzy-ratio comparison itself stays in `service.py`
because rapidfuzz IS a library call, but the *decisions* it feeds into
(canonical-name selection, label-skip filter, embedding-cosine merge
gate, id coercion, id normalization, obvious-merge shortcut) live here
so they're trivially testable."""
from __future__ import annotations

import math
import re
import unicodedata
from typing import Any, Sequence

from .params import EMBED_COSINE_CUTOFF, NUMERIC_LABELS_SKIP


# Pre-compiled regex for collapsing internal whitespace (multiple
# spaces / tabs / newlines → single space). Module-level so we don't
# re-compile per call inside the resolve_entities hot path.
_WS_RE = re.compile(r"\s+")


def pick_canonical(name_a: str, name_b: str) -> tuple[str, str]:
    """Given two near-duplicate entity ids, return `(canonical, duplicate)`.

    Heuristic from deprecated `services/youtube/graph_builder.py:L247-248`:
    the longer name wins ("Saint Kitts and Nevis" beats "St Kitts"). Tie
    goes to `name_a` for stable ordering."""
    if len(name_a) >= len(name_b):
        return name_a, name_b
    return name_b, name_a


def should_skip_fuzzy_label(label: str) -> bool:
    """True for labels whose IDs are numerically- or temporally-distinct
    even when lexically similar (e.g. "$100,000" vs "$1,000,000").

    Mirror of deprecated `SKIP_FUZZY_LABELS` membership check
    (`graph_builder.py:L230-231`)."""
    return label in NUMERIC_LABELS_SKIP


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity between two equal-length real-valued vectors.

    Pure stdlib math — no numpy import to keep `domain.py` zero-dep.
    Returns 0.0 if either vector has zero magnitude (degenerate case;
    safer than raising and forcing the caller to handle it). The
    inputs are typically already L2-normalized at the API boundary,
    but we don't assume it — explicit normalization makes the function
    self-contained for tests."""
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def should_merge_by_cosine(
    cosine: float, cutoff: float = EMBED_COSINE_CUTOFF,
) -> bool:
    """Semantic merge gate — pass iff the cosine clears the empirical
    `EMBED_COSINE_CUTOFF` (default 0.85). Pulled out as a named decision
    so the threshold lives in `params.py` and the call site stays
    readable."""
    return cosine >= cutoff


# ---------- entity-id sanitation (Tier 2 — 2026-06-07) ----------
#
# Three small helpers that harden the entity-resolution pipeline
# against bad input from `LLMGraphTransformer`:
#
#   `coerce_entity_id`   — accept anything the transformer might emit
#                          (str, list, tuple, None, int, etc.), return
#                          a single string. Used at the SOURCE (before
#                          `add_graph_documents`) so the StringArray
#                          ids that broke Step 1's Cypher trim() never
#                          land in Neo4j in the first place. Fix `B`.
#
#   `normalize_entity_id`— canonical form for both Step 1's write-back
#                          and Step 3's safety-net comparison: lowercase
#                          + accent-strip + whitespace-collapse. Fix `F`
#                          (Step 1) and `E` (Step 3 shortcut).
#
#   `is_obvious_merge`   — True iff two ids have IDENTICAL canonical
#                          forms — e.g. `Donald Trump` ↔ `donald trump`
#                          or `São Paulo` ↔ `Sao Paulo`. Step 3 calls
#                          this BEFORE the cosine gate so case-only /
#                          accent-only / whitespace-only diffs merge
#                          unconditionally regardless of BGE-M3's
#                          inconsistent short-string cosine. Fix `E`.

def coerce_entity_id(value: Any) -> str:
    """Coerce whatever `LLMGraphTransformer` emitted as a node `id`
    into a single string. Observed bad shapes:
      - `StringArray` (Python `list`) of alternate-name strings when
        the LLM was uncertain. We take the FIRST element — same
        first-seen-wins behaviour as `dict.fromkeys` deduplication;
        the LLM's primary form is usually the canonical one. Joining
        with " / " would carry the ambiguity into Neo4j as a single
        composite id, polluting the entity graph.
      - `None`. Returns "" so the upstream validation drops the node.
    Pure: no Neo4j, no LLM, no I/O."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        if not value:
            return ""
        first = value[0]
        return str(first) if first is not None else ""
    return str(value)


def normalize_entity_id(value: Any) -> str:
    """Canonical form for comparison + write-back. Pipeline:
      1. Coerce to string (handles `list`/`None`/anything).
      2. NFKD-normalize Unicode (decomposes `é` → `e` + combining acute).
      3. Strip combining marks (drops the accent characters).
      4. `casefold()` (Turkish-I-safe lowercase).
      5. Collapse internal whitespace + trim ends.

    Examples:
      `Petróleo`             → `petroleo`
      `Donald Trump`         → `donald trump`
      `São   Paulo  `        → `sao paulo`
      `["Gastronomia", "Astronomia"]` → `gastronomia`  (via coerce)
    Pure: stdlib-only, no NumPy, no rapidfuzz, no Neo4j."""
    coerced = coerce_entity_id(value)
    if not coerced:
        return ""
    decomposed = unicodedata.normalize("NFKD", coerced)
    stripped = "".join(
        c for c in decomposed if unicodedata.category(c) != "Mn"
    )
    folded = stripped.casefold()
    return _WS_RE.sub(" ", folded).strip()


def is_obvious_merge(a: Any, b: Any) -> bool:
    """True iff `a` and `b` map to the SAME canonical form under
    `normalize_entity_id`. Used as Step 3's pre-fuzz shortcut so
    cosmetic differences (case / accent / whitespace) merge
    unconditionally — independent of BGE-M3's inconsistent
    short-string cosine. Both empty strings → False (don't merge
    junk into junk)."""
    norm_a = normalize_entity_id(a)
    norm_b = normalize_entity_id(b)
    return bool(norm_a) and norm_a == norm_b
