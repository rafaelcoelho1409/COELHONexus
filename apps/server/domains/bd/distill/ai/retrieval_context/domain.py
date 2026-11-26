"""Build and normalize retrieval context records for BD Distill."""
from __future__ import annotations

from typing import Any

from .. import entities


_RESULT_FIELDS = (
    "query",
    "include_web",
    "elasticsearch_result",
    "qdrant_result",
    "neo4j_result",
    "web_search_result",
)
_FUSED_FIELDS = ("evidence", "source_status", "source_queries")
PERSISTED_FIELDS = _RESULT_FIELDS + _FUSED_FIELDS


def source_context(
    book_id: str,
    ingestion_ai_run_id: str,
    counter_run_id: str,
) -> entities.DistillRetrievalContext:
    """Bind Distill run accounting to the ingestion index identity."""
    return entities.DistillRetrievalContext(
        book_id = book_id,
        run_id = ingestion_ai_run_id,
        counter_run_id = counter_run_id,
    )


def normalize_persisted_retrieval_context(value: Any) -> dict[str, Any] | None:
    """Normalize legacy retrieval field names without mutating the input."""
    if not isinstance(value, dict):
        return None
    normalized = dict(value)
    legacy_result = normalized.pop("search_mcp_result", None)
    if "web_search_result" not in normalized and isinstance(legacy_result, dict):
        legacy_result = {**legacy_result}
        if legacy_result.get("source") == "search_mcp":
            legacy_result["source"] = "web_search"
        normalized["web_search_result"] = legacy_result
    for field in ("source_status", "source_queries"):
        values = normalized.get(field)
        if isinstance(values, dict) and "search_mcp" in values:
            web_search_value = values.get("web_search", values["search_mcp"])
            normalized[field] = {
                **{key: item for key, item in values.items() if key not in {"search_mcp", "web_search"}},
                "web_search": web_search_value,
            }
    return normalized


def persisted_retrieval_record(
    state: dict[str, Any],
    fused: dict[str, Any],
) -> dict[str, Any]:
    """Build the run-scoped record containing retrieval results and fusion."""
    record = {key: state.get(key) for key in _RESULT_FIELDS}
    record.update({key: fused[key] for key in _FUSED_FIELDS if key in fused})
    return record
