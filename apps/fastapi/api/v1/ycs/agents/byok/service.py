"""ycs/agents/byok — Redis config read + LLM credential smoke-test."""
from __future__ import annotations

import logging
import time
from typing import Any

from redis.asyncio import Redis

from .domain import build_byok_llm
from .keys   import CONFIG_REDIS_KEY


logger = logging.getLogger(__name__)


async def get_byok_config(redis_aio: Redis) -> dict[str, Any] | None:
    """Read the persisted `LLMConfig` from Redis JSON. Returns `None` on miss, malformed shape, or error."""
    try:
        config = await redis_aio.json().get(CONFIG_REDIS_KEY)
    except Exception as e:
        logger.warning(
            f"[ycs:byok] Redis read failed: {type(e).__name__}: {e}"
        )
        return None
    if not isinstance(config, dict):
        return None
    return config


async def ping_byok(config: dict[str, Any]) -> dict[str, Any]:
    """One `ainvoke("ping")` to validate credentials. Returns `{status, model, ms, reply}` or `{status, error}`."""
    llm = build_byok_llm(config)
    if llm is None:
        return {
            "status": "error",
            "error":  "config incomplete — model and api_key are required",
        }
    start = time.monotonic()
    try:
        response = await llm.ainvoke("ping")
    except Exception as e:
        return {
            "status": "error",
            "error":  f"{type(e).__name__}: {str(e)[:300]}",
        }
    elapsed_ms = int((time.monotonic() - start) * 1000)
    reply = getattr(response, "content", "") or ""
    if isinstance(reply, list):
        reply = " ".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in reply
        )
    return {
        "status": "ok",
        "model":  llm.model,
        "ms":     elapsed_ms,
        "reply":  str(reply)[:200],
    }
