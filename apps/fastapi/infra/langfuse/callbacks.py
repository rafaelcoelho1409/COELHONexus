"""LangChain callback helpers — wrap LangFuse v3's CallbackHandler with
fail-soft defaults and project-aware tagging.

Usage:
    from infra.langfuse.callbacks import build_langchain_callback
    cb = build_langchain_callback(session_id=scan_id,
                                  user_id=profile_id,
                                  tags=["rr", "digest"])
    callbacks = [c for c in (existing_cb, cb) if c is not None]
    await agent.ainvoke(..., config={"callbacks": callbacks})

Returns None when LangFuse is unavailable (package missing or credentials
absent) — callers filter Nones out of their callback list.
"""
from __future__ import annotations

import logging
from typing import Sequence

from .client import is_available


logger = logging.getLogger(__name__)


def build_langchain_callback(
    *,
    session_id: str | None = None,
    user_id:    str | None = None,
    tags:       Sequence[str] | None = None,
):
    """Build a LangChain CallbackHandler that emits to LangFuse, or None
    when the SDK / credentials aren't available."""
    if not is_available():
        return None
    try:
        from langfuse.langchain import CallbackHandler
    except Exception as e:
        logger.debug(
            f"[langfuse] CallbackHandler import failed "
            f"({type(e).__name__}: {e}) — agent runs without LangFuse callback"
        )
        return None
    try:
        kwargs: dict = {}
        if session_id:
            kwargs["session_id"] = session_id
        if user_id:
            kwargs["user_id"] = user_id
        if tags:
            kwargs["tags"] = list(tags)
        return CallbackHandler(**kwargs)
    except Exception as e:
        logger.warning(
            f"[langfuse] CallbackHandler init failed: {type(e).__name__}: {e}"
        )
        return None
