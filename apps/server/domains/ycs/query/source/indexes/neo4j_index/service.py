"""YCS query operations for this retrieval backend."""
from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import Request

import infra

from ..... import ask
from ... import domain, errors, params
from . import patterns

logger = logging.getLogger(__name__)


def _unsupported(backend: str, app: str, q: str) -> dict[str, Any]:
    return domain.unsupported_response(backend, app, q)


def _envelope(
    backend: str,
    app: str,
    q: str,
    hits: list[dict[str, Any]],
    total: int,
    t0: float,
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "backend": backend,
        "app": app,
        "supported": True,
        "namespace": domain.namespace_label(app, backend),
        "q": q,
        "total": total,
        "took_ms": int((time.monotonic() - t0) * 1000),
        "hits": hits,
        "error": error,
    }


def _raw_envelope(
    backend: str,
    app: str,
    t0: float,
    *,
    hits: list[dict[str, Any]] | None = None,
    total: int | None = None,
    notes: list[str] | None = None,
    error: str | None = None,
    ok: bool = True,
) -> dict[str, Any]:
    return {
        "backend": backend,
        "app": app,
        "ok": ok,
        "error": error,
        "notes": list(notes or []),
        "took_ms": int((time.monotonic() - t0) * 1000),
        "total": total,
        "hits": list(hits or []),
    }


def _raw_disallowed(backend: str, app: str, message: str) -> dict[str, Any]:
    return domain.raw_disallowed_response(backend, app, message)


async def query_simple(
    *, app: str, q: str, limit: int, request: Request,
) -> dict[str, Any]:
    """Cypher search over the app's labels.

    Two parameter shapes: `{needle, limit}` for `_*_SEARCH` (CONTAINS
    over title/name/id/text), `{limit}` for `_*_BROWSE` (no filter).
    `needle` is pre-lowered so the Cypher only does `toLower(field)
    CONTAINS $needle` once per field — cheaper than `=~ "(?i)..."`."""
    if not domain.is_supported(app, params.BACKEND_NEO4J):
        return _unsupported(params.BACKEND_NEO4J, app, q)

    raw_q = q.strip()
    cypher = patterns.YCS_CYPHER_SEARCH if raw_q else patterns.YCS_CYPHER_BROWSE

    cypher_params: dict[str, Any] = {"limit": limit}
    if raw_q:
        cypher_params["needle"] = raw_q.lower()

    t0 = time.monotonic()
    try:
        with ask.observability.service.neo4j_query_span(
            operation = "search" if raw_q else "browse",
        ):
            async with infra.neo4j.service.session() as session:
                result = await session.run(cypher, cypher_params)
                records = [dict(record) async for record in result]
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[ycs:query:neo4j] cypher failed: {type(e).__name__}: {e}")
        return _envelope(
            params.BACKEND_NEO4J, app, q, hits = [], total = 0, t0 = t0,
            error = f"{type(e).__name__}: {str(e)[:200]}",
        )

    hits = [domain.project_neo4j_row(r, app = app) for r in records]
    return _envelope(params.BACKEND_NEO4J, app, q, hits, total = len(hits), t0 = t0)


async def run_raw_query(
    *, app: str, body_text: str, request: Request,
) -> dict[str, Any]:
    if not domain.is_supported(app, params.BACKEND_NEO4J):
        return _raw_disallowed(
            params.BACKEND_NEO4J, app,
            f"{app!r} has no presence in Neo4j.",
        )
    try:
        domain.assert_cypher_readonly(body_text)
    except errors.QueryNotAllowed as e:
        return _raw_disallowed(params.BACKEND_NEO4J, app, str(e))

    t0 = time.monotonic()
    try:
        with ask.observability.service.neo4j_query_span(
            operation = "raw_cypher",
        ):
            async with infra.neo4j.service.session(default_access_mode = "READ") as session:
                result = await session.run(body_text)
                records = [dict(r) async for r in result]
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[ycs:query:raw_neo4j] failed: {type(e).__name__}: {e}")
        return _raw_envelope(
            params.BACKEND_NEO4J, app, t0, ok = False,
            error = f"{type(e).__name__}: {str(e)[:400]}",
        )

    projected: list[dict[str, Any]] = []
    for r in records:
        # Neo4j returns rows of named columns. Build a one-line summary
        # by joining the first 2 non-None values; the raw dict goes into
        # `raw` for the renderer (graph / table / json toggle).
        bits: list[str] = []
        for k, v in r.items():
            if v is None:
                continue
            s = str(v)
            bits.append(f"{k}={s[:60]}")
            if len(bits) >= 2:
                break
        projected.append({
            "summary": "  ".join(bits) if bits else "(empty row)",
            "raw":     domain.neo4j_jsonify(r),
        })
    return _raw_envelope(
        params.BACKEND_NEO4J, app, t0,
        hits  = projected,
        total = len(projected),
    )


async def _build_neo4j_schema_live() -> dict[str, Any]:
    """Neo4j live schema — overlay layer for the two-layer merge.

    Output shape (matches the declared floor exactly so merging is a
    straight union):
      · labels                 — list of label names that have ≥1 node
      · relationship_types     — list of rel types with ≥1 instance
      · node_properties        — {label: [{name, types}]}
      · relationship_patterns  — [{src, rel, dst, count}] (actual
                                  observed (src)-[REL]->(dst) triples,
                                  Cypher-runnable, ranked by frequency)
      · node_samples           — {label: [{ id, properties }]} (3 per label)

    EVERY field degrades to empty on an empty Neo4j (the schema procs
    are data-derived), which is exactly the case the declared floor
    is there to cover. Read-only — uses `default_access_mode="READ"`
    defense in depth."""
    out: dict[str, Any] = {
        "labels": [],
        "relationship_types": [],
        "node_properties": {},
        "relationship_patterns": [],
        "node_samples": {},
    }
    try:
        with ask.observability.service.neo4j_query_span(
            operation = "schema_introspect",
        ):
            async with infra.neo4j.service.session(default_access_mode = "READ") as session:
                r = await session.run(patterns.SCHEMA_CYPHER_LABELS)
                row = await r.single()
                out["labels"] = list(row["labels"]) if row else []

                r = await session.run(patterns.SCHEMA_CYPHER_RELS)
                row = await r.single()
                out["relationship_types"] = list(row["rels"]) if row else []

                r = await session.run(patterns.SCHEMA_CYPHER_PROPS)
                props_by_label: dict[str, list[dict[str, Any]]] = {}
                async for row in r:
                    labels = list(row["nodeLabels"] or [])
                    name   = row["propertyName"]
                    types  = list(row["propertyTypes"] or [])
                    if not name:
                        continue
                    for lab in labels:
                        props_by_label.setdefault(lab, []).append({
                            "name":  name,
                            "types": types,
                        })
                out["node_properties"] = props_by_label

                # Real connectivity — frequency-ranked. This is the
                # single highest-ROI add for AI grounding: the LLM sees
                # `(Document)-[MENTIONS]->(__Entity__) x 18402` rather
                # than guessing that `:MENTIONS` might exist.
                try:
                    r = await session.run(patterns.SCHEMA_CYPHER_REL_PATTERNS)
                    async for row in r:
                        out["relationship_patterns"].append({
                            "src":   row["src"],
                            "rel":   row["rel"],
                            "dst":   row["dst"],
                            "count": int(row["n"] or 0),
                        })
                except Exception as e:  # noqa: BLE001
                    logger.debug(f"[ycs:query:schema:neo4j] rel patterns failed: {e}")

                # Sample 3 nodes per label. Capped to first 10 labels to
                # keep schema size bounded; the rest get an empty list.
                for lab in (out["labels"] or [])[:10]:
                    try:
                        r = await session.run(
                            patterns.SCHEMA_CYPHER_LABEL_SAMPLES, {"label": lab},
                        )
                        samples: list[dict[str, Any]] = []
                        async for row in r:
                            n = row["n"]
                            props = dict(n) if n else {}
                            samples.append({
                                "id":         n.element_id if n else None,
                                "properties": domain.truncate_doc(props),
                            })
                        out["node_samples"][lab] = samples
                    except Exception as e:  # noqa: BLE001
                        logger.debug(
                            f"[ycs:query:schema:neo4j] samples for {lab!r} failed: {e}",
                        )
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {str(e)[:200]}"
    return out


async def _build_neo4j_schema() -> dict[str, Any]:
    """Two-layer Neo4j schema: declared floor + live overlay.

    Highest-value case for the two-layer split: Neo4j's introspection
    procedures (`db.labels()`, `db.relationshipTypes()`,
    `db.schema.nodeTypeProperties()`) are entirely data-derived — an
    empty graph returns NOTHING. Without the declared floor the AI has
    zero structural grounding on day-zero or after a wipe.

    Declared floor (`domain.declared_neo4j_schema`) is sourced from
    the actual writer code in `domains/ycs/rag/source/indexes/neo4j_index/` so it
    matches what the Docling Graph extractor + the YCS-specific
    `build_video_metadata_graph` produce."""
    declared = domain.declared_neo4j_schema()
    try:
        live = await _build_neo4j_schema_live()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[ycs:query:schema:neo4j] live fetch failed: {type(e).__name__}: {e}")
        return declared
    return domain.merge_neo4j_schema(declared, live)


async def query_schema() -> dict[str, Any]:
    """Return the declared and live schema for the YCS neo4j store."""
    return await _build_neo4j_schema()


async def validate_query(body_text: str) -> tuple[bool, str | None]:
    """Check read-only Cypher and ask Neo4j to build its execution plan."""
    valid, error = domain.check_with_safety(body_text, backend = params.BACKEND_NEO4J)
    if not valid:
        return False, error
    try:
        async with infra.neo4j.service.session(default_access_mode = "READ") as session:
            result = await session.run(f"EXPLAIN {body_text}")
            summary = await result.consume()
        warnings = [
            notification
            for notification in getattr(summary, "notifications", []) or []
            if any(
                warning in str(getattr(notification, "code", ""))
                for warning in (
                    "UnknownLabelWarning",
                    "UnknownRelationshipTypeWarning",
                    "UnknownPropertyKeyWarning",
                )
            )
        ]
        if warnings:
            details = "; ".join(
                str(getattr(item, "description", "Unknown graph label, relationship, or property"))
                for item in warnings
            )
            return False, f"Neo4j query does not match the live graph schema: {details[:300]}"
    except Exception as error:  # noqa: BLE001 - return details for one repair attempt.
        return False, f"Neo4j rejected the query plan: {type(error).__name__}: {str(error)[:300]}"
    return True, None
