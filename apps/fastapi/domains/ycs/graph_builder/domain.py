"""ycs/graph_builder — PURE entity-resolution helpers.

Functional Core (`docs/CODE-CONVENTIONS.md` §4): no Neo4j, no LLM, no
rapidfuzz import. The fuzzy-ratio comparison itself stays in `service.py`
because rapidfuzz IS a library call, but the *decisions* it feeds into
(canonical-name selection, label-skip filter, embedding-cosine merge
gate, id coercion, id normalization, obvious-merge shortcut) live here
so they're trivially testable."""
from __future__ import annotations
from . import params, patterns

import math
import unicodedata
from typing import Any, Sequence


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
    even when lexically similar (e.g. "$100,000" vs "$1,000,000").py:L230-231`)."""
    return label in params.NUMERIC_LABELS_SKIP


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
    cosine: float, cutoff: float = params.EMBED_COSINE_CUTOFF,
) -> bool:
    """Semantic merge gate — pass iff the cosine clears the empirical
    `EMBED_COSINE_CUTOFF` (default 0.85). Pulled out as a named decision
    so the threshold lives in `params.py` and the call site stays
    readable."""
    return cosine >= cutoff


# Three small helpers that harden the entity-resolution pipeline
# against bad input from `LLMGraphTransformer`:
#   `coerce_entity_id`   — accept anything the transformer might emit
#                          (str, list, tuple, None, int, etc.), return
#                          a single string. Used at the SOURCE (before
#                          `add_graph_documents`) so the StringArray
#                          ids that broke Step 1's Cypher trim() never
#                          land in Neo4j in the first place. Fix `B`.
#   `normalize_entity_id`— canonical form for both Step 1's write-back
#                          and Step 3's safety-net comparison: lowercase
#                          + accent-strip + whitespace-collapse. Fix `F`
#                          (Step 1) and `E` (Step 3 shortcut).
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


def sanitize_neo4j_label(value: Any, fallback: str = "Entity") -> str:
    """Coerce whatever `LLMGraphTransformer` emitted as a node/relationship
    `type` into something Neo4j's kernel will actually accept as a label/
    relationship-type token.

    2026-09-14: root-caused a live failure —
    `apoc.create.addLabels`/`apoc.merge.relationship` (both called with
    the type string taken verbatim from the LLM's output, completely
    unsanitized by langchain-neo4j's `add_graph_documents`) threw
    `Neo.ClientError.Procedure.ProcedureCallFailed` wrapping
    `org.neo4j.internal.kernel.api.exceptions.schema.
    IllegalTokenNameException` — poisoning the ENTIRE video's write
    (one write, all nodes+rels for that video) because the exception
    aborted the whole `add_graph_documents` call, not just the one bad
    node. Neo4j label/type tokens must be non-empty and must not
    contain NUL/control characters; the exact offending value wasn't
    recoverable (the caller's own error-log truncation cut it off
    before the kernel's specific reason), so this is deliberately
    defensive rather than targeting one exact bad shape: strip control
    chars, collapse to a safe fallback if empty, cap length. Pure: no
    Neo4j, no LLM, no I/O."""
    coerced = coerce_entity_id(value).strip()
    # Strip NUL + other C0/C1 control characters — the class of
    # character most likely to trip IllegalTokenNameException; ordinary
    # spaces/punctuation/unicode are fine as Neo4j label tokens.
    cleaned = "".join(ch for ch in coerced if ch.isprintable() or ch == " ")
    cleaned = cleaned.strip()
    if not cleaned:
        return fallback
    # Neo4j's own token-name limit is far higher than this, but a
    # pathological multi-KB "type" string is a model failure, not a
    # real label — cap it so it can't distort the schema either way.
    return cleaned[:200]


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
    return patterns.WS_RE.sub(" ", folded).strip()


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


def is_infra_error(err: str | None) -> bool:
    """True for provider/infra failures worth retrying (timeouts, 5xx,
    rate limits, connection/overload). Everything else — auth,
    context-length, schema/validation, code bugs — will not heal on an
    immediate re-attempt (DD's classifier split, adapted). Pure so both
    `service.py` (logging) and `neo4j_task/task.py` (streak halt) share
    one definition."""
    m = (err or "").lower()
    if any(s in m for s in (
        "context", "auth", "401", "403", "filter", "schema",
        "valid", "attributerror", "keyerror", "typeerror",
    )):
        return False
    return (
        "timeout" in m or "504" in m or "503" in m or "502" in m
        or "429" in m or "rate" in m or "connection" in m
        or "overload" in m or "unavailable" in m
    )


def is_overflow_error(err: str | None) -> bool:
    """True when the failure signals context-window overflow (split the
    input and retry per segment instead of hammering the full doc)."""
    m = (err or "").lower()
    return (
        ("context" in m and "length" in m)
        or "too many tokens" in m or "max_tokens" in m
        or "context_length" in m or "contextwindow" in m
    )
