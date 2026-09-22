"""ycs/query — pure projection helpers (Functional Core).

Per docs/CODE-CONVENTIONS.md §4: no I/O, no async, no logging. Same
inputs → same outputs. The projectors here take raw store responses
(ES `_source` dicts, Qdrant points, Neo4j records) and produce
uniform `QueryHit` dicts so the imperative shell in `service.py`
stays a thin orchestrator."""
from __future__ import annotations
from . import entities, errors, params, patterns

import infra

import json
import re
from typing import Any


def _snippet(text: str | None) -> str:
    """Cap free-text fields so a single hit can't bloat the response."""
    if not text:
        return ""
    text = str(text).strip()
    if len(text) <= params.SNIPPET_CHARS:
        return text
    return text[:params.SNIPPET_CHARS].rstrip() + "…"


# Elasticsearch — YCS only (metadata + transcriptions). The two indexes
# have different shapes so we route by the `_index` ES echoes back on
# every hit. Names live in `infra.elasticsearch.keys` (single truth).


def project_es_hit(hit: dict[str, Any], app: str = params.APP_YCS) -> dict[str, Any]:
    """ES `{_index, _id, _score, _source}` → `QueryHit` dict.

    Two-index branching: the metadata index carries the human-friendly
    `title` + `webpage_url`; the transcriptions index carries the
    `content` + a `video_id` foreign key. Title falls back to the
    video_id so transcript hits don't render with an empty title."""
    src   = hit.get("_source", {}) or {}
    index = hit.get("_index", "")
    hit_id = str(hit.get("_id", ""))
    score = hit.get("_score")

    if index == infra.elasticsearch.keys.INDEX_TRANSCRIPTIONS:
        video_id = src.get("video_id") or hit_id.split("_")[0]
        title    = f"Transcript · {video_id} ({src.get('lang') or 'n/a'})"
        snippet  = _snippet(src.get("content"))
        url      = f"https://www.youtube.com/watch?v={video_id}" if video_id else ""
    else:
        # Metadata index (or any future index that follows its shape).
        title   = src.get("title") or hit_id
        snippet = _snippet(src.get("description"))
        url     = src.get("webpage_url") or ""

    return {
        "kind":    params.BACKEND_ES,
        "app":     app,
        "id":      hit_id,
        "title":   title,
        "snippet": snippet,
        "score":   float(score) if isinstance(score, (int, float)) else None,
        "url":     url,
        "extra":   {
            "index":   index,
            "_source": src,
        },
    }


# Qdrant — YCS (`youtube-transcripts`) and RR (`radar_papers`). Both
# collections embed via the same NIM model (2048d cosine) so query-side
# embedding is a shared path; only payload shape differs.
def project_qdrant_point(point: Any, app: str) -> dict[str, Any]:
    """Qdrant point (ScoredPoint or Record) → `QueryHit` dict.

    YCS payload: `content / video_id / title / channel / webpage_url`.
    RR  payload: `arxiv_id / title / authors / categories / signal`."""
    payload = (getattr(point, "payload", None) or {}) if not isinstance(point, dict) else point.get("payload", {})
    pid     = str(getattr(point, "id", "") if not isinstance(point, dict) else point.get("id", ""))
    score   = getattr(point, "score", None) if not isinstance(point, dict) else point.get("score")

    if app == params.APP_RR:
        arxiv_id = payload.get("arxiv_id") or pid
        title    = payload.get("title") or arxiv_id
        snippet  = _snippet(payload.get("abstract") or payload.get("content"))
        url      = f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else ""
    else:
        # YCS — youtube-transcripts collection
        video_id = payload.get("video_id") or ""
        chunk    = payload.get("chunk_index")
        title    = payload.get("title") or video_id
        if chunk is not None and video_id:
            title = f"{title}  · chunk {chunk}"
        snippet  = _snippet(payload.get("content"))
        url      = payload.get("webpage_url") or (
            f"https://www.youtube.com/watch?v={video_id}" if video_id else ""
        )

    return {
        "kind":    params.BACKEND_QDRANT,
        "app":     app,
        "id":      pid,
        "title":   title,
        "snippet": snippet,
        "score":   float(score) if isinstance(score, (int, float)) else None,
        "url":     url,
        "extra":   {"payload": payload},
    }


# Neo4j — YCS (Document/Video/Channel/__Entity__) and RR (Paper/Author/
# Concept/Source). The Cypher in `service.py` returns a uniform projection
# dict; the helper below just re-shapes it into a QueryHit.
def project_neo4j_row(row: dict[str, Any], app: str) -> dict[str, Any]:
    """Cypher row → `QueryHit`.

    Expected row shape (built by service-side Cypher):
      `{label, key, title, snippet, url, properties}`."""
    label      = row.get("label") or ""
    key        = str(row.get("key") or "")
    title      = row.get("title") or key
    snippet    = _snippet(row.get("snippet"))
    url        = row.get("url") or ""
    properties = row.get("properties") or {}

    return {
        "kind":    params.BACKEND_NEO4J,
        "app":     app,
        "id":      f"{label}:{key}" if label else key,
        "title":   title,
        "snippet": snippet,
        "score":   None,
        "url":     url,
        "extra":   {
            "label":      label,
            "properties": properties,
        },
    }


# Two-layer schema declared floor (structural contract) — the "floor" of
# the two-layer schema (live + declared) the AI prompt grounds on. Why we
# need a declared floor:
#
#   · ES   — mappings come back from `GET _mapping` even on an empty
#            index, so the floor is mostly redundant there. But if the
#            cluster is briefly unreachable we still want the AI to
#            know the field shapes.
#   · Qdrant — `get_collection` returns the declared payload-index
#            keys, but the FULL payload shape (unindexed fields like
#            `content`, `chunk_index`) is only observable via `scroll`.
#            Empty collection → no observable keys → AI flies blind.
#   · Neo4j — `db.labels()` / `db.relationshipTypes()` / `db.schema.*`
#            only return what EXISTS. An empty graph returns nothing.
#            Without a declared floor the AI has no shape to ground on
#            at day-zero.
#
# Sources of truth (so the declared schema stays in sync with what
# gets WRITTEN to each store):
#
#   ES     → infra/elasticsearch/schemas.py  (METADATA_MAPPING,
#                                               TRANSCRIPTIONS_MAPPING)
#   Qdrant → domains.ycs.ingestion.domain     (build_payload — the
#                                               writer's payload shape)
#   Neo4j  → domains/ycs/graph_builder/*       + the entity-merger Cypher
#            (build_video_metadata_graph creates Video+Channel+BELONGS_TO;
#             LLMGraphTransformer creates __Entity__ + Document with
#             the .video_id tag).
#
# Update these functions when the writer shape changes.


def declared_es_schema() -> dict[str, Any]:
    """Pull the canonical mappings out of `infra/elasticsearch/schemas.py`
    so we never drift from what `ensure_indexes()` actually creates."""
    import infra
    return {
        "indices": {
            infra.elasticsearch.keys.INDEX_METADATA: {
                "mappings":     infra.elasticsearch.schemas.METADATA_MAPPING["mappings"],
                "doc_count":    0,
                "samples":      [],
                "field_values": {},
            },
            infra.elasticsearch.keys.INDEX_TRANSCRIPTIONS: {
                "mappings":     infra.elasticsearch.schemas.TRANSCRIPTIONS_MAPPING["mappings"],
                "doc_count":    0,
                "samples":      [],
                "field_values": {},
            },
        },
    }


def declared_qdrant_schema() -> dict[str, Any]:
    """YCS Qdrant collection — vectors are NAMED (`dense` + `sparse`)
    so the AI knows to use `("dense", vector)` tuples on raw search.
    Payload shape from `build_payload` (the writer in
    `domains.ycs.ingestion.domain`).

    `text_indexed_fields` is empty because the YCS ingestion bootstrap
    only creates KEYWORD indexes on `channel_id` + `video_id`. Without
    a TEXT index, Qdrant's `match: {text: ...}` operator fails at the
    Pydantic layer ("Extra inputs not permitted"). The prompt
    renderer surfaces this so the LLM stops generating bogus
    `match_text` / `match: {text: ...}` filters."""
    import domains
    return {
        "collections": [{
            "name":           domains.ycs.ingestion.params.QDRANT_COLLECTION,
            "points_count":   0,
            "vectors_config": {
                "dense":  {"size": 2048, "distance": "Cosine"},
                "sparse": {"distance": "BM25 (sparse)"},
            },
            "payload_schema": {
                # `channel_id` + `video_id` are the indexed keys
                # ingestion sets up at bootstrap; everything else is
                # in the payload but unindexed.
                "channel_id": {"data_type": "keyword"},
                "video_id":   {"data_type": "keyword"},
            },
            "observed_payload_keys": list(params.QDRANT_EXPECTED_PAYLOAD_KEYS),
            # Fields with a TEXT payload index — required for
            # `match: {text: ...}`. YCS ingestion creates none today;
            # for full-text search on transcripts the user should
            # switch to the Elasticsearch backend.
            "text_indexed_fields":   [],
            "samples":               [],
        }],
    }


def declared_neo4j_schema() -> dict[str, Any]:
    """YCS graph shape — what the writers actually create:

      · LLMGraphTransformer  → (:__Entity__) + (:Document) +
                               (Document)-[:MENTIONS]->(__Entity__)
      · build_video_metadata_graph → (:Video) + (:Channel) +
                               (Video)-[:BELONGS_TO]->(Channel)

    Property names confirmed by reading `graph_builder/service.py`
    (lines 804-823) + the retriever's Cypher (`retriever/neo4j.py`)."""
    return {
        "labels": ["__Entity__", "Document", "Video", "Channel"],
        "relationship_types": ["MENTIONS", "BELONGS_TO"],
        "node_properties": {
            "Video": [
                {"name": "id",          "types": ["String"]},
                {"name": "title",       "types": ["String"]},
                {"name": "channel_id",  "types": ["String"]},
                {"name": "upload_date", "types": ["String"]},
                {"name": "webpage_url", "types": ["String"]},
            ],
            "Channel": [
                {"name": "id",   "types": ["String"]},
                {"name": "name", "types": ["String"]},
            ],
            "Document": [
                # LangChain's GraphDocument writer keeps the doc's
                # `text` and our YCS code stamps `.video_id`
                # explicitly so re-ingest can skip processed videos.
                {"name": "video_id", "types": ["String"]},
                {"name": "text",     "types": ["String"]},
            ],
            "__Entity__": [
                {"name": "id",          "types": ["String"]},
                {"name": "description", "types": ["String"]},
            ],
        },
        # Declared relationships — `count=None` flag = "we know this
        # pattern exists in the writer code; we just don't know how
        # many instances are live". Live discovery fills in the
        # count + may add patterns the writer didn't predict (e.g.
        # the LLMGraphTransformer invents inter-entity rels with
        # type names derived from the LLM's output).
        "relationship_patterns": [
            {"src": "Document", "rel": "MENTIONS",   "dst": "__Entity__", "count": None, "declared": True},
            {"src": "Video",    "rel": "BELONGS_TO", "dst": "Channel",    "count": None, "declared": True},
        ],
        "node_samples": {},
    }


# Raw-DSL read-only safety guards. Each helper takes a raw user payload
# and either returns it (passes) or raises `errors.QueryNotAllowed` with
# a human-readable reason. The router maps that exception to HTTP 400 so
# the editor can show the message inline.
#
# Patterns are conservative — false positives (refuse a benign query)
# are preferred to false negatives (allow a write through). When in
# doubt, refuse.


def _strip_cypher_strings(query: str) -> str:
    return patterns.CYPHER_STRING.sub(" ", query)


def assert_cypher_readonly(query: str) -> None:
    """Raise `errors.QueryNotAllowed` if the Cypher query looks like it writes.

    Implementation note — we tokenize on word boundaries + case-fold,
    then check each token against the keyword set. Easier to reason
    about than a giant alternation regex; cheap enough on Cypher-sized
    inputs (kilobytes)."""
    if not query.strip():
        raise errors.QueryNotAllowed("Empty Cypher.")

    scan = _strip_cypher_strings(query).lower()

    # Comments — strip line + block comments so a write keyword inside
    # `// CREATE ...` doesn't trigger.
    scan = re.sub(r"//[^\n]*", " ",  scan)
    scan = re.sub(r"/\*.*?\*/", " ", scan, flags = re.DOTALL)

    tokens = re.findall(r"[a-z_][a-z0-9_]*", scan)
    bad = sorted({t for t in tokens if t in params.CYPHER_WRITE_KEYWORDS})
    if bad:
        raise errors.QueryNotAllowed(
            f"Cypher contains write keyword(s): {', '.join(bad)}. "
            "The Query page is read-only — use MATCH / RETURN only.",
        )
    m = patterns.CYPHER_WRITE_PROC.search(scan)
    if m:
        raise errors.QueryNotAllowed(
            f"Cypher calls a write/mutation procedure ({m.group(0).strip()}). "
            "Only read procedures (db.labels(), db.schema.*, apoc.meta.*) "
            "are allowed.",
        )


def parse_es_body(text: str) -> entities.ParsedESBody:
    """Parse + validate an ES query body.

    Accepted shape — the JSON body you'd POST to `_search`. We DON'T
    accept the URL path here; the server pins the path to `_search` on
    the YCS indexes, so the user can only ever issue a read. The body
    is checked for:
      - valid JSON
      - top-level `query` clause (refuse free-form `script`/`update`/
        `delete_by_query` payloads if the user smuggles them in)
      - `size <= ES_MAX_SIZE` (default to `ES_MAX_SIZE/10` when absent)
    """
    if not text.strip():
        raise errors.QueryNotAllowed("Empty Elasticsearch body.")
    try:
        body = json.loads(text)
    except json.JSONDecodeError as e:
        raise errors.QueryNotAllowed(f"Invalid JSON: {e.msg} at line {e.lineno} col {e.colno}")
    if not isinstance(body, dict):
        raise errors.QueryNotAllowed(
            "Elasticsearch body must be a JSON object (got "
            f"{type(body).__name__}).",
        )
    # Refuse the obvious write shapes — `delete_by_query` and
    # `update_by_query` use the same JSON body but a different URL; the
    # user might mistakenly paste a body intended for them.
    for forbidden in ("script", "scripted_metric"):
        if forbidden in body:
            raise errors.QueryNotAllowed(
                f"`{forbidden}` clauses are blocked (write surface).",
            )

    synth = False
    if "size" not in body:
        body["size"] = params.ES_MAX_SIZE // 10
        synth = True
    else:
        try:
            n = int(body["size"])
        except (TypeError, ValueError):
            raise errors.QueryNotAllowed(f"`size` must be an integer, got {body['size']!r}.")
        if n < 0:
            raise errors.QueryNotAllowed("`size` must be >= 0.")
        if n > params.ES_MAX_SIZE:
            raise errors.QueryNotAllowed(
                f"`size` is {n} — the Query page caps at {params.ES_MAX_SIZE} "
                "to keep response payloads bounded.",
            )

    return entities.ParsedESBody(body = body, synth_size = synth)


def parse_qdrant_body(text: str) -> entities.ParsedQdrantOp:
    """Accept a Qdrant body shaped as:

        { "op": "search" | "scroll" | "query_points" | "count",
          ... body for that op ... }

    The `op` discriminator picks which client method we call; the
    remainder is forwarded as kwargs. Only read ops are listed in
    `params.QDRANT_READ_OPS`, so writes (`upsert`, `delete`, `update`) are
    rejected by virtue of not being in the set."""
    if not text.strip():
        raise errors.QueryNotAllowed("Empty Qdrant body.")
    try:
        body = json.loads(text)
    except json.JSONDecodeError as e:
        raise errors.QueryNotAllowed(f"Invalid JSON: {e.msg} at line {e.lineno} col {e.colno}")
    if not isinstance(body, dict):
        raise errors.QueryNotAllowed(
            f"Qdrant body must be a JSON object (got {type(body).__name__}).",
        )
    op = body.get("op", "search")
    if op not in params.QDRANT_READ_OPS:
        raise errors.QueryNotAllowed(
            f"Qdrant op {op!r} is not allowed. "
            f"Read-only ops: {', '.join(params.QDRANT_READ_OPS)}.",
        )

    # Clamp limit / size. Different op names use different keys —
    # `limit` (most), `count` (count). Always synthesize one.
    if op in ("search", "scroll", "query_points"):
        limit = body.get("limit", 20)
        try:
            n = int(limit)
        except (TypeError, ValueError):
            raise errors.QueryNotAllowed(f"`limit` must be an integer, got {limit!r}.")
        if n < 1:
            raise errors.QueryNotAllowed("`limit` must be >= 1.")
        if n > params.QDRANT_MAX_LIMIT:
            raise errors.QueryNotAllowed(
                f"`limit` is {n} — Query page caps at {params.QDRANT_MAX_LIMIT}.",
            )
        body["limit"] = n

    return entities.ParsedQdrantOp(op = op, body = body)


def check_with_safety(text: str, *, backend: str) -> tuple[bool, str | None]:
    """Apply the same safety guards the Run path uses — so the AI
    output passes the EXACT same gate as user-typed content. Returns
    `(ok, error)`."""
    try:
        if backend == params.BACKEND_NEO4J:
            assert_cypher_readonly(text)
        elif backend == params.BACKEND_ES:
            parse_es_body(text)
        elif backend == params.BACKEND_QDRANT:
            parse_qdrant_body(text)
    except errors.QueryNotAllowed as e:
        return False, str(e)
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    return True, None


# Pure response-shape helpers (Functional Core half of `service.py`'s
# query/raw-query orchestration).


def unsupported_response(backend: str, app: str, q: str) -> dict[str, Any]:
    """Same shape as a real response — `supported=False` is the only
    signal the UI needs to render the "no data here" state. Returns a
    kwargs dict for `schemas.QueryResponse(**...)` rather than the
    schema instance itself, so this stays free of the schemas import
    for callers that only want the raw shape."""
    return {
        "backend":   backend,
        "app":       app,
        "supported": False,
        "namespace": "",
        "q":         q,
        "hits":      [],
    }


def raw_disallowed_response(backend: str, app: str, msg: str) -> dict[str, Any]:
    """Validation rejection kwargs for `schemas.RawQueryResponse(**...)` —
    `ok=False` so the editor's error band lights up."""
    return {
        "backend": backend,
        "app":     app,
        "ok":      False,
        "error":   msg,
    }


def translate_search_body(body: dict[str, Any]) -> dict[str, Any]:
    """Legacy `search()` kwargs → `query_points()` kwargs.

    `query_vector` (search's arg) becomes `query` (+ `using` when the
    old body named a vector). Handles the three shapes a hand-typed or
    AI-generated raw body might use for a named vector: a `{"name":
    ..., "vector": ...}` dict (NamedVector's JSON shape), a `[name,
    vector]` pair (the old Python-tuple convention, JSON-serialized as
    a 2-element list), or a bare vector (unnamed collection)."""
    out = dict(body)
    qv = out.pop("query_vector", None)
    query, using = qv, None
    if isinstance(qv, dict) and "name" in qv and "vector" in qv:
        using, query = qv["name"], qv["vector"]
    elif (
        isinstance(qv, list) and len(qv) == 2
        and isinstance(qv[0], str) and isinstance(qv[1], list)
    ):
        using, query = qv
    out["query"] = query
    if using is not None:
        out["using"] = using
    return out


def _extract_balanced_json(s: str) -> str:
    """Walk from the first `{` to its matching `}`, ignoring braces
    inside string literals (handles escaped quotes). Returns the
    enclosed JSON or `s` unchanged if no balanced object is found —
    cheap defense against prose around the JSON body."""
    start = s.find("{")
    if start < 0:
        return s
    depth = 0
    in_str = False
    esc    = False
    for i, ch in enumerate(s[start:], start = start):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return s[start: i + 1]
    return s


def extract_cypher(s: str) -> str:
    """Pull just the Cypher out of a response that may also contain
    prose, fenced blocks, or other commentary.

    Real-world failure mode (2026-06-16, "best graph about Brasil"):
    the LLM wrote prose around the Cypher — e.g. "I'll create a query
    that…" / "this will delete duplicates from the result". The prose
    contained `create` / `delete`, which the safety regex flagged as
    write ops, rejecting an otherwise-valid READ-only query.

    Extraction strategy (each step strictly stronger than the last):
      1. Fenced block ANYWHERE — triple-backtick blocks, optionally
         tagged ``cypher`` / ``cql``. Strong signal; almost never
         matches inside natural prose.
      2. Per-LINE scan for a line that STARTS with a Cypher clause
         pattern (MATCH `(`, OPTIONAL MATCH `(`, CALL foo.bar`(`, or
         WITH/UNWIND/RETURN + identifier). The next-token requirement
         filters out prose words like "match the entity then …".
      3. Last-resort: substring search for `MATCH (` or `OPTIONAL
         MATCH (` with the opening paren mandatory — that's about as
         strong a Cypher signal as you can get in a single token.
      4. Failing all three, return the input verbatim and let the
         safety regex deliver a precise rejection message."""
    if not s:
        return s
    # 1. Fenced extraction.
    fence = re.search(
        r"```(?:cypher|cql)?\s*\n?(.*?)```",
        s, flags = re.DOTALL | re.IGNORECASE,
    )
    if fence:
        return fence.group(1).strip()
    # 2. Per-line scan for the FIRST line that opens a Cypher clause.
    #    The next-token after the keyword (`(` or `\S`) is what
    #    distinguishes "MATCH (x)" from a prose word like "match".
    _CYPHER_LINE_START = re.compile(
        r"^\s*(?:"
        r"MATCH\s*\("                       # MATCH (
        r"|OPTIONAL\s+MATCH\s*\("           # OPTIONAL MATCH (
        r"|WITH\s+\S"                       # WITH x
        r"|UNWIND\s+\S"                     # UNWIND list
        r")",
        flags = re.IGNORECASE,
    )
    lines = s.split("\n")
    for i, line in enumerate(lines):
        if _CYPHER_LINE_START.match(line):
            return "\n".join(lines[i:]).strip()
    # 3. Substring fallback — only the strongest signals (`KEYWORD (`)
    #    to avoid grabbing prose words.
    m = re.search(
        r"\b(?:MATCH|OPTIONAL\s+MATCH)\s*\(",
        s, flags = re.IGNORECASE,
    )
    if m:
        return s[m.start():].strip()
    # 4. Give up — let safety speak.
    return s


def _format_cypher(s: str) -> str:
    """Cypher polish — inject newlines before major clause keywords so
    a single-line model response renders multi-line + legible.

    Pipeline:
      1. Normalize line endings + strip.
      2. PROTECT string literals + `//` + `/* */` comments by swapping
         them out for placeholders — so we never insert newlines inside
         a quoted token (e.g. `RETURN \"what to RETURN\"` shouldn't
         break in the middle of the string).
      3. Inject a newline before each major Cypher keyword that's
         preceded by inline whitespace. Compound keywords first
         (`OPTIONAL MATCH`, `UNION ALL`, `ORDER BY`) so they win over
         the bare single-word variants.
      4. Restore the protected literals.
      5. rstrip per line + collapse blank-line runs of 3+.

    Case-preserving: the matched keyword text is reused verbatim in the
    replacement (the LLM might output lowercase Cypher; we don't
    silently upper-case it)."""
    if not s.strip():
        return ""
    src = s.replace("\r\n", "\n").strip()

    # 1. Protect strings + comments.
    placeholders: list[str] = []
    def _protect(match: "re.Match[str]") -> str:
        placeholders.append(match.group(0))
        return f"\x00P{len(placeholders) - 1}\x00"

    _protect_re = re.compile(
        r"//[^\n]*"                       # line comment
        r"|/\*.*?\*/"                     # block comment (non-greedy)
        r"|'(?:\\.|[^'\\])*'"             # single-quoted string
        r"|\"(?:\\.|[^\"\\])*\""          # double-quoted string
        r"|`(?:\\.|[^`\\])*`",            # back-tick identifier
        flags = re.DOTALL,
    )
    protected = _protect_re.sub(_protect, src)

    # 2. Inject newlines before major clauses. SINGLE alternation regex
    #    with longest-first ordering so `OPTIONAL MATCH` wins over the
    #    bare `MATCH` rule and doesn't get split in half (the two-pass
    #    version produced `OPTIONAL\nMATCH`). `re.sub` does left-to-
    #    right non-overlapping matching, so once "OPTIONAL MATCH" is
    #    consumed at position N the next search resumes past the
    #    compound keyword.
    _KEYWORDS = (
        "OPTIONAL MATCH", "UNION ALL", "ORDER BY",
        "MATCH", "WHERE", "WITH", "RETURN", "LIMIT", "SKIP",
        "UNION", "UNWIND", "CALL", "YIELD",
    )
    _alts = sorted(
        # Spaces in keywords become `\s+` so `OPTIONAL\nMATCH` or
        # `OPTIONAL  MATCH` (multiple spaces) still match as one
        # compound clause.
        (kw.replace(" ", r"\s+") for kw in _KEYWORDS),
        key = len, reverse = True,
    )
    _all_kw_re = re.compile(
        r"(?<!\n)[ \t]+(" + "|".join(_alts) + r")\b",
        flags = re.IGNORECASE,
    )
    protected = _all_kw_re.sub(lambda m: "\n" + m.group(1), protected)

    # 3. Restore protected spans.
    out = re.sub(
        r"\x00P(\d+)\x00",
        lambda m: placeholders[int(m.group(1))],
        protected,
    )

    # 4. Final normalization.
    out = "\n".join(line.rstrip() for line in out.split("\n"))
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def post_clean(text: str, *, backend: str) -> str:
    """Strip markdown fences + leading/trailing prose the model sometimes
    can't help adding, then PRETTY-FORMAT the result so the editor
    shows it indented + readable.

      - JSON backends → balance braces to extract the JSON object,
        then `json.loads` + `json.dumps(indent=2)` so the final body
        is canonical-pretty (2-space indent, stable key order, no
        trailing whitespace). Falls back to the raw extract if the
        text doesn't parse — that path also gets surfaced by the
        safety guard and triggers a self-repair retry.
      - Cypher → rstrip per line, collapse blank-line runs > 2,
        and strip leading/trailing whitespace. The few-shot
        exemplars already teach the LLM the line-per-clause style;
        we just polish what came back.
    """
    if not text:
        return ""
    s = text.strip()

    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s
    if s.endswith("```"):
        s = s.rsplit("```", 1)[0]
    s = s.strip()

    if backend in (params.BACKEND_ES, params.BACKEND_QDRANT):
        extracted = _extract_balanced_json(s)
        try:
            obj = json.loads(extracted)
        except json.JSONDecodeError:
            # Common LLM mistake: trailing commas before `}` / `]`.
            # Standard JSON forbids them; one permissive sweep recovers
            # the most frequent failure mode without pulling in json5.
            relaxed = re.sub(r",(\s*[}\]])", r"\1", extracted)
            try:
                obj = json.loads(relaxed)
            except json.JSONDecodeError:
                # Truly malformed — return raw so the safety guard
                # surfaces a precise error and self-repair fires.
                return extracted
        # ensure_ascii=False so unicode survives the round-trip;
        # indent=2 matches the scaffold templates in editor.js so the
        # editor renders cleanly after replacement.
        return json.dumps(obj, indent = 2, ensure_ascii = False)

    if backend == params.BACKEND_NEO4J:
        return _format_cypher(extract_cypher(s))

    return s


def merge_es_schema(declared: dict[str, Any], live: dict[str, Any]) -> dict[str, Any]:
    """Per-index overlay. Declared mappings are kept ONLY when live is
    missing/erroring (live wins on mappings to track schema drift);
    samples + field_values + doc_count come from live."""
    out: dict[str, Any] = {"indices": {}}
    for name, idx in (declared.get("indices") or {}).items():
        out["indices"][name] = dict(idx)
    for name, idx in (live.get("indices") or {}).items():
        cur = out["indices"].get(name, {})
        if idx.get("error"):
            # Live had an error for this index — keep declared floor
            cur["error"] = idx["error"]
            out["indices"][name] = cur
            continue
        if idx.get("mappings"):
            cur["mappings"] = idx["mappings"]
        cur["doc_count"]    = idx.get("doc_count", cur.get("doc_count", 0))
        cur["samples"]      = idx.get("samples")      or cur.get("samples", [])
        cur["field_values"] = idx.get("field_values") or cur.get("field_values", {})
        out["indices"][name] = cur
    return out


def merge_qdrant_schema(declared: dict[str, Any], live: dict[str, Any]) -> dict[str, Any]:
    """Per-collection overlay. Declared payload-key list is the floor
    (union with whatever the live scroll observed). Live vectors_config
    + samples + points_count overlay. `text_indexed_fields` is derived
    from the declared payload_schema where data_type == 'text'; live
    can add to it if a field has a text index in production that the
    declared floor doesn't list."""
    declared_cols = {c["name"]: c for c in (declared.get("collections") or [])}
    live_cols     = {c["name"]: c for c in (live.get("collections")     or [])}
    out_cols: list[dict[str, Any]] = []
    for name in (set(declared_cols) | set(live_cols)):
        d = declared_cols.get(name, {})
        l = live_cols.get(name, {})
        if l.get("error"):
            merged = dict(d)
            merged["error"] = l["error"]
            out_cols.append(merged)
            continue
        text_idx: set[str] = set(d.get("text_indexed_fields") or [])
        text_idx.update(l.get("text_indexed_fields") or [])
        for field, cfg in (l.get("payload_schema") or {}).items():
            dt = str((cfg or {}).get("data_type") or "").lower()
            if dt.startswith("text"):
                text_idx.add(field)
        merged = {
            "name":           name,
            "points_count":   l.get("points_count", d.get("points_count", 0)),
            "vectors_config": l.get("vectors_config") or d.get("vectors_config"),
            "payload_schema": l.get("payload_schema") or d.get("payload_schema") or {},
            "observed_payload_keys": sorted(
                set(d.get("observed_payload_keys") or [])
                | set(l.get("observed_payload_keys") or [])
            ),
            "text_indexed_fields": sorted(text_idx),
            "samples": l.get("samples") or d.get("samples") or [],
        }
        out_cols.append(merged)
    return {"collections": out_cols}


def merge_neo4j_schema(declared: dict[str, Any], live: dict[str, Any]) -> dict[str, Any]:
    """Union of labels + rel types; union of properties per label; merge
    of relationship_patterns marking declared-but-unobserved with
    `count=null`, declared-AND-observed with the live count, and
    live-only (LLM-invented inter-entity rels) untouched."""
    if live.get("error") and not (live.get("labels") or live.get("relationship_types")):
        # Live call totally failed — pass declared with the error annotated.
        out = dict(declared)
        out["error"] = live["error"]
        return out

    labels = sorted(set(declared.get("labels") or []) | set(live.get("labels") or []))
    rels   = sorted(
        set(declared.get("relationship_types") or [])
        | set(live.get("relationship_types") or [])
    )

    node_props: dict[str, list[dict[str, Any]]] = {}
    for source in (declared.get("node_properties") or {}, live.get("node_properties") or {}):
        for label, props in source.items():
            current = {p["name"]: p for p in node_props.get(label, [])}
            for p in (props or []):
                # live overrides declared on property types (schema drift is real)
                current[p["name"]] = p
            node_props[label] = list(current.values())

    # Live count takes precedence; declared-only entries keep count=None to signal structurally valid but empty.
    pat: dict[tuple[str, str, str], dict[str, Any]] = {}
    for p in (declared.get("relationship_patterns") or []):
        key = (p.get("src", ""), p.get("rel", ""), p.get("dst", ""))
        pat[key] = {**p, "declared": True}
    for p in (live.get("relationship_patterns") or []):
        key = (p.get("src", ""), p.get("rel", ""), p.get("dst", ""))
        if key in pat:
            pat[key] = {**pat[key], "count": p.get("count"), "observed": True}
        else:
            pat[key] = {**p, "observed": True}
    def _pat_sort(p: dict[str, Any]):
        c = p.get("count")
        return (-(c if c is not None else -1), p.get("src", ""), p.get("rel", ""))
    rel_patterns = sorted(pat.values(), key = _pat_sort)

    node_samples: dict[str, list[dict[str, Any]]] = {}
    for source in (declared.get("node_samples") or {}, live.get("node_samples") or {}):
        for label, samples in source.items():
            if samples:
                node_samples[label] = samples
            elif label not in node_samples:
                node_samples[label] = []

    out: dict[str, Any] = {
        "labels":                labels,
        "relationship_types":    rels,
        "node_properties":       node_props,
        "relationship_patterns": rel_patterns,
        "node_samples":          node_samples,
    }
    if live.get("error"):
        out["error"] = live["error"]
    return out


def truncate_doc(src: dict, *, max_field_chars: int = 240) -> dict:
    """Trim long string fields in a sample `_source` so the prompt
    stays under-budget. Lists are shallow-truncated to 3 items."""
    out: dict[str, Any] = {}
    for k, v in (src or {}).items():
        if isinstance(v, str):
            out[k] = v if len(v) <= max_field_chars else v[:max_field_chars] + "…"
        elif isinstance(v, list):
            out[k] = v[:3]
        elif isinstance(v, dict):
            out[k] = {ik: iv for ik, iv in list(v.items())[:6]}
        elif isinstance(v, (int, float, bool)) or v is None:
            out[k] = v
        else:
            out[k] = str(v)
    return out


def neo4j_value(v, *, Node, Rel, Path):
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, Node):
        return {
            "_kind":      "node",
            "id":         v.element_id,
            "labels":     list(v.labels),
            "properties": dict(v),
        }
    if isinstance(v, Rel):
        return {
            "_kind":      "relationship",
            "id":         v.element_id,
            "type":       v.type,
            "start":      v.start_node.element_id if v.start_node else None,
            "end":        v.end_node.element_id   if v.end_node   else None,
            "properties": dict(v),
        }
    if isinstance(v, Path):
        return {
            "_kind": "path",
            "nodes": [neo4j_value(n, Node = Node, Rel = Rel, Path = Path) for n in v.nodes],
            "rels":  [neo4j_value(r, Node = Node, Rel = Rel, Path = Path) for r in v.relationships],
        }
    if isinstance(v, (list, tuple)):
        return [neo4j_value(x, Node = Node, Rel = Rel, Path = Path) for x in v]
    if isinstance(v, dict):
        return {k: neo4j_value(x, Node = Node, Rel = Rel, Path = Path) for k, x in v.items()}
    return str(v)


def neo4j_jsonify(row: dict) -> dict:
    """Neo4j driver returns Node / Relationship objects that aren't
    directly JSON-serializable. Convert each row into a plain `dict` of
    primitives preserving the graph-shape under `_node` / `_relationship`
    keys so the frontend can build a Cytoscape graph view from them."""
    from neo4j.graph import Node, Path, Relationship
    out: dict = {}
    for k, v in row.items():
        out[k] = neo4j_value(v, Node = Node, Rel = Relationship, Path = Path)
    return out


def is_supported(app: str, backend: str) -> bool:
    """True when the (app, backend) pair has data we can query."""
    return params.APP_BACKENDS.get(app, {}).get(backend, entities.AppNamespace(False)).available


def namespace_label(app: str, backend: str) -> str:
    """Human-readable label for the (app, backend) target — used in the
    response's `namespace` field. Empty when unsupported."""
    return params.APP_BACKENDS.get(app, {}).get(backend, entities.AppNamespace(False)).label
