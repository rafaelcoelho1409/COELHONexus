"""Generate, validate, and repair read-only BD retrieval queries."""
from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import ai

from .. import source

logger = logging.getLogger(__name__)


async def generate_query_stream(
    *,
    backend: str,
    user_prompt: str,
    previous: str,
) -> AsyncIterator[dict[str, str]]:
    try:
        async for event in _generate_query_events(
            backend = backend,
            user_prompt = user_prompt,
            previous = previous,
        ):
            if event["event"] == "result":
                result = event["data"]
                yield {"data": json.dumps({"event": "chunk", "data": result["query"]})}
                yield {"data": json.dumps({
                    "event": "done",
                    "ok": result["valid"],
                    "final": result["query"],
                    "error": result["validation_error"],
                })}
            else:
                yield {"data": json.dumps(event)}
    except Exception as error:  # noqa: BLE001 - LLM/provider errors are sent to the SSE client.
        logger.warning(f"[bd:query:ai] generation failed: {type(error).__name__}: {error}")
        yield {"data": json.dumps({"event": "error", "error": f"{type(error).__name__}: {error}"})}


async def draft_query(
    *,
    backend: str,
    user_prompt: str,
    previous: str = "",
) -> dict[str, Any]:
    """Draft and validate one query using the same flow as the HTTP workbench."""
    async for event in _generate_query_events(
        backend = backend,
        user_prompt = user_prompt,
        previous = previous,
    ):
        if event["event"] == "result":
            return event["data"]
    raise RuntimeError("The BD Query drafter returned no result")


async def _generate_query_events(
    *,
    backend: str,
    user_prompt: str,
    previous: str,
) -> AsyncIterator[dict[str, Any]]:
    if backend not in source.params.BACKENDS:
        raise ValueError(f"Unsupported backend: {backend}.")
    schema = await source.service.query_schema(backend)
    schema_fingerprint = source.domain.schema_fingerprint(schema)
    response = await ai.bd.agents.query.drafting.service.generate_query(
        backend = backend,
        user_prompt = user_prompt,
        previous = previous,
        schema = schema,
    )
    model_name = (getattr(response, "response_metadata", None) or {}).get("model_name")
    if model_name:
        yield {"event": "model", "data": str(model_name)}
    candidate = _response_text(backend, response)
    valid, error = await source.service.validate_query(backend, candidate, schema = schema)
    attempts = 1
    if not valid:
        yield {"event": "repair"}
        response = await ai.bd.agents.query.drafting.service.generate_query(
            backend = backend,
            user_prompt = user_prompt,
            previous = candidate or previous,
            schema = schema,
            repair_error = error,
        )
        model_name = (getattr(response, "response_metadata", None) or {}).get("model_name") or model_name
        candidate = _response_text(backend, response)
        valid, error = await source.service.validate_query(backend, candidate, schema = schema)
        attempts = 2
    yield {
        "event": "result",
        "data": {
            "backend": backend,
            "query": candidate,
            "valid": valid,
            "validation_error": error,
            "model": str(model_name) if model_name else None,
            "attempts": attempts,
            "schema_fingerprint": schema_fingerprint,
        },
    }


def _response_text(backend: str, response: Any) -> str:
    content = getattr(response, "content", response)
    if (
        backend == source.params.BACKEND_NEO4J
        and isinstance(content, dict)
        and isinstance(content.get("query"), str)
    ):
        return content["query"].strip()
    if isinstance(content, list):
        content = "\n".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in content
        )
    text = str(content or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 2 and lines[-1].strip() == "```":
            text = "\n".join(lines[1:-1]).strip()
    if backend == source.params.BACKEND_NEO4J:
        text = source.domain.normalize_neo4j_query(text)
    return text
