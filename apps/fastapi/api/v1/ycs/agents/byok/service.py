"""ycs/agents/byok — Redis read + LLM smoke-test (I/O shell).

Two responsibilities:
  - `get_byok_config(redis_aio)` — read the persisted `LLMConfig` dict.
    Returns `None` on miss, malformed shape, or Redis error so callers
    can fall back silently.
  - `ping_byok(config)` — build a `ChatLiteLLM` from the config and fire
    one cheap `ainvoke("ping")` to validate the credentials BEFORE the
    user commits to using them for a full RAG query. Returns a small
    result dict the `POST /agents/config/test` endpoint relays verbatim."""
from __future__ import annotations

import logging
import time
from typing import Any

from redis.asyncio import Redis

from .domain import build_byok_llm
from .keys   import CONFIG_REDIS_KEY


logger = logging.getLogger(__name__)


async def get_byok_config(redis_aio: Redis) -> dict[str, Any] | None:
    """Read the persisted `LLMConfig` from Redis JSON. Returns `None` if
    the key is missing, malformed, or unreachable — caller is expected
    to fall back to the rotator chain in that case."""
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
    """Single `ainvoke("ping")` round-trip to validate a BYOK config.

    Returned shape (relayed verbatim by `POST /agents/config/test`):
      - `{"status": "ok",    "model": "...", "ms": int, "reply": "..."}`
      - `{"status": "error", "error": "..."}`

    On schema-incomplete input the caller's endpoint translates this to
    a 400 — `ping_byok` only signals semantic outcomes."""
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
