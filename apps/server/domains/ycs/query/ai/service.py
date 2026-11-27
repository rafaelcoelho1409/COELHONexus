"""Generate and validate read-only queries for YCS retrieval stores."""
from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from fastapi import Request

import ai

from ... import ask
from .. import source

logger = logging.getLogger(__name__)


async def generate_query_stream(
    *,
    backend: str,
    user_prompt: str,
    previous: str,
    request: Request | None = None,
) -> AsyncIterator[dict[str, str]]:
    """Draft a query from live schema and self-repair one rejected attempt."""
    yield {"data": json.dumps({"event": "start", "phase": "generate"})}
    with source.service.query_span("ai.generate", backend = backend, app = "ycs"):
        try:
            cached_schema = await source.service.query_schema(backend, request = request)
            schema = {key: value for key, value in cached_schema.items() if key != "cached_at"}
            response = await ai.ycs.agents.query.service.generate_query(
                backend = backend,
                user_prompt = user_prompt,
                previous = previous,
                schema = schema,
            )
            await _capture_usage(response)
            model_name = (getattr(response, "response_metadata", None) or {}).get("model_name")
            if model_name:
                yield {"data": json.dumps({"event": "model", "model": str(model_name)})}

            candidate = _response_text(backend, response)
            valid, error = await source.service.validate_query(backend, candidate, schema = schema)
            attempts = 1
            if not valid:
                yield {"data": json.dumps({"event": "repair", "error": error})}
                attempts = 2
                response = await ai.ycs.agents.query.service.generate_query(
                    backend = backend,
                    user_prompt = user_prompt,
                    previous = candidate or previous,
                    schema = schema,
                    repair_error = error,
                )
                await _capture_usage(response)
                candidate = _response_text(backend, response)
                valid, error = await source.service.validate_query(backend, candidate, schema = schema)

            yield {"data": json.dumps({"event": "chunk", "data": candidate})}
            yield {"data": json.dumps({
                "event": "done",
                "ok": valid,
                "final": candidate,
                "error": error,
                "attempts": attempts,
            })}
        except Exception as error:  # noqa: BLE001 - provider failures are reported to the SSE client.
            logger.warning(f"[ycs:query:ai] generation failed: {type(error).__name__}: {error}")
            yield {"data": json.dumps({"event": "error", "error": f"{type(error).__name__}: {error}"})}


async def _capture_usage(response: Any) -> None:
    """Keep query-agent usage in YCS's existing LLM accounting pipeline."""
    try:
        await ask.rag.service.capture_llm_usage(response)
    except Exception:
        logger.debug("[ycs:query:ai] usage capture failed", exc_info = True)


def _response_text(backend: str, response: Any) -> str:
    """Extract and normalize one store query from the DeepAgent response."""
    content = getattr(response, "content", response)
    if (
        backend == source.params.BACKEND_NEO4J
        and isinstance(content, dict)
        and isinstance(content.get("query"), str)
    ):
        content = content["query"]
    if isinstance(content, list):
        content = "\n".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in content
        )
    return source.domain.post_clean(str(content or ""), backend = backend)
