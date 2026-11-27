"""Read-only Elasticsearch query and schema operations for BD."""
from __future__ import annotations

import logging
import time
from typing import Any

import infra

from ... import domain, errors, params

logger = logging.getLogger(__name__)


async def run_raw_query(body_text: str) -> dict[str, Any]:
    backend = params.BACKEND_ELASTICSEARCH
    try:
        parsed = domain.parse_elasticsearch_body(body_text)
    except errors.QueryNotAllowed as error:
        return domain.raw_response(backend, ok = False, error = str(error))

    started = time.monotonic()
    notes = []
    if parsed.synth_size:
        notes.append("`size` was omitted; the safe default was applied.")
    try:
        client = infra.elasticsearch.service.get_es()
        result = await client.search(
            index = ",".join(params.ELASTICSEARCH_INDEX_NAMES),
            body = parsed.body,
        )
    except Exception as error:  # noqa: BLE001 - backend failures belong in the query result envelope.
        logger.warning(f"[bd:query:elasticsearch] failed: {type(error).__name__}: {error}")
        return domain.raw_response(
            backend,
            ok = False,
            error = f"{type(error).__name__}: {str(error)[:300]}",
            notes = notes,
            took_ms = int((time.monotonic() - started) * 1000),
        )

    raw_hits = result.get("hits", {}).get("hits", [])
    total = result.get("hits", {}).get("total", 0)
    if isinstance(total, dict):
        total = total.get("value", 0)
    hits = []
    for item in raw_hits:
        source_fields = item.get("_source", {}) or {}
        section_path = source_fields.get("section_path") or []
        if isinstance(section_path, list):
            section_path = " › ".join(str(section) for section in section_path)
        summary = source_fields.get("title") or section_path or source_fields.get("book_id") or item.get("_id", "")
        hits.append({"summary": str(summary), "raw": item})
    return domain.raw_response(
        backend,
        notes = notes,
        took_ms = int((time.monotonic() - started) * 1000),
        total = int(total or 0),
        hits = hits,
    )


async def query_schema() -> dict[str, Any]:
    mappings = {
        infra.elasticsearch.keys.INDEX_BD_INGESTION: infra.elasticsearch.schemas.BD_INGESTION_MAPPING["mappings"],
        infra.elasticsearch.keys.INDEX_BD_RAG_PASSAGES: infra.elasticsearch.schemas.BD_RAG_PASSAGES_MAPPING["mappings"],
    }
    indices: dict[str, Any] = {}
    live_mapping_count = 0
    try:
        client = infra.elasticsearch.service.get_es()
    except Exception as error:  # noqa: BLE001 - return declared mappings when the client is unavailable.
        logger.debug(f"[bd:query:schema:elasticsearch] client lookup failed: {error}")
        return {
            "indices": {
                name: {"mappings": mapping, "doc_count": None}
                for name, mapping in mappings.items()
            },
            "live_schema_available": False,
            "live_data_available": False,
            "live_mapping_count": 0,
        }
    for name in params.ELASTICSEARCH_INDEX_NAMES:
        mapping = mappings[name]
        doc_count = None
        try:
            live = await client.indices.get_mapping(index = name)
            mapping = (live.get(name) or {}).get("mappings") or mapping
            live_mapping_count += 1
        except Exception as error:  # noqa: BLE001 - keep the declared mapping if ES is unavailable.
            logger.debug(f"[bd:query:schema:elasticsearch] mapping lookup failed for {name}: {error}")
        try:
            count = await client.count(index = name)
            doc_count = int(count.get("count", 0))
        except Exception as error:  # noqa: BLE001 - schema remains useful when counts are unavailable.
            logger.debug(f"[bd:query:schema:elasticsearch] count lookup failed for {name}: {error}")
        indices[name] = {"mappings": mapping, "doc_count": doc_count}
    return {
        "indices": indices,
        "live_schema_available": live_mapping_count > 0,
        "live_data_available": any(
            isinstance(index.get("doc_count"), int) and index["doc_count"] > 0
            for index in indices.values()
        ),
        "live_mapping_count": live_mapping_count,
    }


async def validate_query(body_text: str) -> tuple[bool, str | None]:
    """Use Elasticsearch's validator to check generated search syntax and fields."""
    valid, error = domain.check_query_body(params.BACKEND_ELASTICSEARCH, body_text)
    if not valid:
        return False, error
    try:
        parsed = domain.parse_elasticsearch_body(body_text)
        client = infra.elasticsearch.service.get_es()
        result = await client.indices.validate_query(
            index = ",".join(params.ELASTICSEARCH_INDEX_NAMES),
            body = {"query": parsed.body.get("query", {"match_all": {}})},
            explain = True,
        )
    except Exception as error:  # noqa: BLE001 - return backend validation details for one repair attempt.
        return False, f"Elasticsearch rejected query validation: {type(error).__name__}: {str(error)[:300]}"
    if result.get("valid", False):
        return True, None
    explanations = result.get("explanations") or []
    details = "; ".join(
        str(item.get("error") or item.get("explanation") or "")
        for item in explanations
        if isinstance(item, dict) and (item.get("error") or item.get("explanation"))
    )
    return False, f"Elasticsearch marked the query invalid{': ' + details if details else '.'}"
