"""YCS Neo4j index pure helpers for entity resolution and evidence checks.

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
from collections.abc import Sequence
from typing import Any

from langchain_text_splitters import RecursiveCharacterTextSplitter

from . import config, params, patterns


def graph_chunks(content: str, *, chunk_size: int | None = None) -> list[str]:
    """Split transcript text into bounded, slightly overlapping graph inputs."""
    if not content.strip():
        return []
    size = chunk_size or config.GRAPH_CHUNK.input_chars
    splitter = RecursiveCharacterTextSplitter(
        chunk_size = size,
        chunk_overlap = min(config.GRAPH_CHUNK.overlap_chars, size - 1),
        add_start_index = True,
        separators = ["\n\n", "\n", " ", ""],
    )
    return [
        chunk.page_content.strip()
        for chunk in splitter.create_documents([content])
        if chunk.page_content.strip()
    ]


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
    immediate re-attempt. Pure so both
    `service.py` (logging) and `neo4j_task/task.py` (streak halt) share
    one definition."""
    m = (err or "").lower()
    # Honor the HTTP status before inspecting the provider's response body:
    # gateways may return an internal 500 whose body mentions AttributeError,
    # which is not an application code failure and should still be retried.
    if re.search(r"\b(?:429|500|502|503|504)\b", m):
        return True
    if any(s in m for s in (
        "context", "auth", "401", "403", "filter", "schema",
        "valid", "attributerror", "keyerror", "typeerror",
    )):
        return False
    return (
        "timeout" in m or "rate" in m or "connection" in m
        or "overload" in m or "unavailable" in m
    )


def is_overflow_error(err: str | None) -> bool:
    """True when an LLM length limit calls for splitting and retrying input."""
    m = (err or "").lower()
    return (
        ("context" in m and "length" in m)
        or "too many tokens" in m or "max_tokens" in m
        or "context_length" in m or "contextwindow" in m
        or "lengthfinishreasonerror" in m
        or "length limit was reached" in m
        or "finish_reason=length" in m
    )


def _graph_value(candidate: Any, name: str, default: Any = None) -> Any:
    return candidate.get(name, default) if isinstance(candidate, dict) else getattr(candidate, name, default)


def _normalized_excerpt(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def _contains_normalized_entity(entity_key: str, text_key: str) -> bool:
    return bool(
        entity_key
        and re.search(rf"(?<!\w){re.escape(entity_key)}(?!\w)", text_key)
    )


def _fallback_relation_is_local(source_id: str, target_id: str, transcript: str) -> bool:
    source_key = normalize_entity_id(source_id)
    target_key = normalize_entity_id(target_id)
    if not source_key or not target_key:
        return False
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", transcript):
        sentence_key = normalize_entity_id(sentence)
        if (
            _contains_normalized_entity(source_key, sentence_key)
            and _contains_normalized_entity(target_key, sentence_key)
        ):
            return True
    return False


def validate_graph_candidates(
    candidate: Any,
    transcript: str,
    *,
    require_evidence: bool,
) -> tuple[list[dict[str, str]], list[dict[str, str]], list[str]]:
    """Return bounded graph facts grounded in a transcript.

    Structured transformer output must include exact evidence properties.
    The provider compatibility path has no property schema, so it instead
    requires each entity name to occur in the transcript and both relation
    endpoints to occur in one sentence. Graph library objects stay outside
    this functional core; the return values are plain fact dictionaries.
    """
    transcript_key = _normalized_excerpt(transcript)
    nodes_by_id: dict[str, dict[str, str]] = {}
    issues: list[str] = []
    for node in _graph_value(candidate, "nodes", []) or []:
        entity_id = coerce_entity_id(_graph_value(node, "id")).strip()
        entity_type = sanitize_neo4j_label(_graph_value(node, "type"))
        properties = _graph_value(node, "properties", {})
        properties = properties if isinstance(properties, dict) else {}
        evidence_value = properties.get("evidence")
        if isinstance(evidence_value, (list, tuple)):
            evidence = " ".join(str(item) for item in evidence_value if item is not None).strip()
        else:
            evidence = str(evidence_value or "").strip()
        if require_evidence:
            grounded = bool(evidence) and _normalized_excerpt(evidence) in transcript_key
        else:
            entity_key = normalize_entity_id(entity_id)
            grounded = _contains_normalized_entity(entity_key, normalize_entity_id(transcript))
        if (
            not entity_id
            or len(entity_id) > 300
            or len(entity_type) > 100
            or len(evidence) > 800
            or not grounded
        ):
            issues.append(f"Entity {entity_id!r} has no supported transcript evidence.")
            continue
        key = entity_id.casefold()
        existing = nodes_by_id.get(key)
        if existing:
            if existing["type"].casefold() != entity_type.casefold():
                issues.append(
                    f"Entity {entity_id!r} was assigned conflicting types "
                    f"{existing['type']!r} and {entity_type!r}."
                )
            continue
        if len(nodes_by_id) >= params.MAX_EXTRACTED_NODES:
            issues.append("The extraction exceeded the node limit.")
            continue
        nodes_by_id[key] = {"id": entity_id, "type": entity_type}

    accepted_relationships: list[dict[str, str]] = []
    seen_relationships: set[tuple[str, str, str]] = set()
    for relation in _graph_value(candidate, "relationships", []) or []:
        source = _graph_value(relation, "source")
        target = _graph_value(relation, "target")
        source_id = coerce_entity_id(_graph_value(source, "id")).strip()
        target_id = coerce_entity_id(_graph_value(target, "id")).strip()
        relation_type = sanitize_neo4j_label(
            _graph_value(relation, "type"), fallback = "RELATED_TO",
        )
        properties = _graph_value(relation, "properties", {})
        properties = properties if isinstance(properties, dict) else {}
        evidence_value = properties.get("evidence")
        if isinstance(evidence_value, (list, tuple)):
            evidence = " ".join(str(item) for item in evidence_value if item is not None).strip()
        else:
            evidence = str(evidence_value or "").strip()
        source_node = nodes_by_id.get(source_id.casefold())
        target_node = nodes_by_id.get(target_id.casefold())
        if require_evidence:
            grounded = bool(evidence) and _normalized_excerpt(evidence) in transcript_key
        else:
            grounded = _fallback_relation_is_local(source_id, target_id, transcript)
        if (
            not source_node
            or not target_node
            or len(source_id) > 300
            or len(target_id) > 300
            or len(relation_type) > 100
            or len(evidence) > 800
            or not grounded
        ):
            issues.append(
                f"Relationship {source_id!r} -> {target_id!r} has unsupported evidence or endpoints."
            )
            continue
        key = (source_id.casefold(), relation_type.casefold(), target_id.casefold())
        if key in seen_relationships:
            continue
        if len(accepted_relationships) >= params.MAX_EXTRACTED_RELATIONSHIPS:
            issues.append("The extraction exceeded the relationship limit.")
            continue
        seen_relationships.add(key)
        accepted_relationships.append({
            "source_id": source_node["id"],
            "target_id": target_node["id"],
            "type": relation_type,
        })
    return list(nodes_by_id.values()), accepted_relationships, issues
