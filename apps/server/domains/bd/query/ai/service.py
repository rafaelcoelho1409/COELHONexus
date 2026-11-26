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
        schema = await source.service.query_schema(backend)
        response = await ai.bd.agents.query.drafting.service.generate_query(
            backend = backend,
            user_prompt = user_prompt,
            previous = previous,
            schema = schema,
        )
        candidate = _response_text(backend, response)
        model_name = (getattr(response, "response_metadata", None) or {}).get("model_name")
        if model_name:
            yield {"data": json.dumps({"event": "model", "data": str(model_name)})}
        valid, error = await source.service.validate_query(backend, candidate, schema = schema)
        if not valid:
            yield {"data": json.dumps({"event": "repair"})}
            response = await ai.bd.agents.query.drafting.service.generate_query(
                backend = backend,
                user_prompt = user_prompt,
                previous = candidate or previous,
                schema = schema,
                repair_error = error,
            )
            candidate = _response_text(backend, response)
            valid, error = await source.service.validate_query(backend, candidate, schema = schema)
        yield {"data": json.dumps({"event": "chunk", "data": candidate})}
        yield {"data": json.dumps({
            "event": "done",
            "ok": valid,
            "final": candidate,
            "error": error,
        })}
    except Exception as error:  # noqa: BLE001 - LLM/provider errors are sent to the SSE client.
        logger.warning(f"[bd:query:ai] generation failed: {type(error).__name__}: {error}")
        yield {"data": json.dumps({"event": "error", "error": f"{type(error).__name__}: {error}"})}


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
