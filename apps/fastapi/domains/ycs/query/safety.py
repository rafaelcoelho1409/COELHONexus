"""ycs/query — read-only safety guards for raw user-supplied queries.

The Query page is read-only by design (see docs/CODE-CONVENTIONS.md
anti-patterns + `feedback_no_deep_research_for_design.md` philosophy:
explore freely, never let an exploration UI mutate state).

Each helper takes a raw user payload and either returns it (passes) or
raises `QueryNotAllowed` with a human-readable reason. The router maps
that exception to HTTP 400 so the editor can show the message inline.

Patterns are conservative — false positives (refuse a benign query)
are preferred to false negatives (allow a write through). When in
doubt, refuse."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass


class QueryNotAllowed(Exception):
    """Raised when a raw query fails the read-only checks."""


# Cypher
# Match a write-keyword as a WHOLE TOKEN (word boundaries) outside of
# string literals. Matched in lowercase against a literal-stripped copy
# of the query so a sneaky `"CREATE ..."` inside a string property doesn't
# graph-projection procedures that mutate state.
_CYPHER_WRITE_KEYWORDS: tuple[str, ...] = (
    "create", "merge", "delete", "set", "remove",
    "drop", "load", "foreach", "detach",
)

# APOC write surface — anything under `apoc.create.*`, `apoc.merge.*`,
# `apoc.refactor.*`, `apoc.periodic.iterate(... CREATE ...)` etc. Plus
# GDS catalog mutate calls. Plain `apoc.meta.*` / `db.labels()` / etc.
# are allowed (they're read paths).
_CYPHER_WRITE_PROC = re.compile(
    r"\bcall\s+("
    r"apoc\.(create|merge|refactor|nodes\.delete|periodic|trigger|atomic)|"
    r"db\.(create|drop|index\.fulltext\.(create|drop))|"
    r"gds\..*\.(write|mutate)|"
    r"dbms\."
    r")",
    flags = re.IGNORECASE,
)

# before scanning for keywords. Cypher escapes are `\"` / `\\` etc.
_CYPHER_STRING = re.compile(
    r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|`(?:\\.|[^`\\])*`",
)


def _strip_cypher_strings(query: str) -> str:
    return _CYPHER_STRING.sub(" ", query)


def assert_cypher_readonly(query: str) -> None:
    """Raise `QueryNotAllowed` if the Cypher query looks like it writes.

    Implementation note — we tokenize on word boundaries + case-fold,
    then check each token against the keyword set. Easier to reason
    about than a giant alternation regex; cheap enough on Cypher-sized
    inputs (kilobytes)."""
    if not query.strip():
        raise QueryNotAllowed("Empty Cypher.")

    scan = _strip_cypher_strings(query).lower()

    # Comments — strip line + block comments so a write keyword inside
    # `// CREATE ...` doesn't trigger.
    scan = re.sub(r"//[^\n]*", " ",  scan)
    scan = re.sub(r"/\*.*?\*/", " ", scan, flags = re.DOTALL)

    tokens = re.findall(r"[a-z_][a-z0-9_]*", scan)
    bad = sorted({t for t in tokens if t in _CYPHER_WRITE_KEYWORDS})
    if bad:
        raise QueryNotAllowed(
            f"Cypher contains write keyword(s): {', '.join(bad)}. "
            "The Query page is read-only — use MATCH / RETURN only.",
        )
    m = _CYPHER_WRITE_PROC.search(scan)
    if m:
        raise QueryNotAllowed(
            f"Cypher calls a write/mutation procedure ({m.group(0).strip()}). "
            "Only read procedures (db.labels(), db.schema.*, apoc.meta.*) "
            "are allowed.",
        )


# Elasticsearch
@dataclass(frozen=True, slots=True)
class ParsedESBody:
    """Validated ES request payload. Holds the raw dict (returned to the
    transport) plus a flag for whether we synthesized a default `size`."""
    body:           dict
    synth_size:     bool


_ES_MAX_SIZE = 200


def parse_es_body(text: str) -> ParsedESBody:
    """Parse + validate an ES query body.

    Accepted shape — the JSON body you'd POST to `_search`. We DON'T
    accept the URL path here; the server pins the path to `_search` on
    the YCS indexes, so the user can only ever issue a read. The body
    is checked for:
      - valid JSON
      - top-level `query` clause (refuse free-form `script`/`update`/
        `delete_by_query` payloads if the user smuggles them in)
      - `size <= _ES_MAX_SIZE` (default to `_ES_MAX_SIZE/10` when absent)
    """
    if not text.strip():
        raise QueryNotAllowed("Empty Elasticsearch body.")
    try:
        body = json.loads(text)
    except json.JSONDecodeError as e:
        raise QueryNotAllowed(f"Invalid JSON: {e.msg} at line {e.lineno} col {e.colno}")
    if not isinstance(body, dict):
        raise QueryNotAllowed(
            "Elasticsearch body must be a JSON object (got "
            f"{type(body).__name__}).",
        )
    # Refuse the obvious write shapes — `delete_by_query` and
    # `update_by_query` use the same JSON body but a different URL; the
    # user might mistakenly paste a body intended for them.
    for forbidden in ("script", "scripted_metric"):
        if forbidden in body:
            raise QueryNotAllowed(
                f"`{forbidden}` clauses are blocked (write surface).",
            )

    synth = False
    if "size" not in body:
        body["size"] = _ES_MAX_SIZE // 10
        synth = True
    else:
        try:
            n = int(body["size"])
        except (TypeError, ValueError):
            raise QueryNotAllowed(f"`size` must be an integer, got {body['size']!r}.")
        if n < 0:
            raise QueryNotAllowed("`size` must be >= 0.")
        if n > _ES_MAX_SIZE:
            raise QueryNotAllowed(
                f"`size` is {n} — the Query page caps at {_ES_MAX_SIZE} "
                "to keep response payloads bounded.",
            )

    return ParsedESBody(body = body, synth_size = synth)


# Qdrant
@dataclass(frozen=True, slots=True)
class ParsedQdrantOp:
    """One of {`search`, `scroll`, `query_points`} + the validated body."""
    op:   str
    body: dict


_QDRANT_READ_OPS: tuple[str, ...] = ("search", "scroll", "query_points", "count")
_QDRANT_MAX_LIMIT = 200


def parse_qdrant_body(text: str) -> ParsedQdrantOp:
    """Accept a Qdrant body shaped as:

        { "op": "search" | "scroll" | "query_points" | "count",
          ... body for that op ... }

    The `op` discriminator picks which client method we call; the
    remainder is forwarded as kwargs. Only read ops are listed in
    `_QDRANT_READ_OPS`, so writes (`upsert`, `delete`, `update`) are
    rejected by virtue of not being in the set."""
    if not text.strip():
        raise QueryNotAllowed("Empty Qdrant body.")
    try:
        body = json.loads(text)
    except json.JSONDecodeError as e:
        raise QueryNotAllowed(f"Invalid JSON: {e.msg} at line {e.lineno} col {e.colno}")
    if not isinstance(body, dict):
        raise QueryNotAllowed(
            f"Qdrant body must be a JSON object (got {type(body).__name__}).",
        )
    op = body.get("op", "search")
    if op not in _QDRANT_READ_OPS:
        raise QueryNotAllowed(
            f"Qdrant op {op!r} is not allowed. "
            f"Read-only ops: {', '.join(_QDRANT_READ_OPS)}.",
        )

    # Clamp limit / size. Different op names use different keys —
    # `limit` (most), `count` (count). Always synthesize one.
    if op in ("search", "scroll", "query_points"):
        limit = body.get("limit", 20)
        try:
            n = int(limit)
        except (TypeError, ValueError):
            raise QueryNotAllowed(f"`limit` must be an integer, got {limit!r}.")
        if n < 1:
            raise QueryNotAllowed("`limit` must be >= 1.")
        if n > _QDRANT_MAX_LIMIT:
            raise QueryNotAllowed(
                f"`limit` is {n} — Query page caps at {_QDRANT_MAX_LIMIT}.",
            )
        body["limit"] = n

    return ParsedQdrantOp(op = op, body = body)
