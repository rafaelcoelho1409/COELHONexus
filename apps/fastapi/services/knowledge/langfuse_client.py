"""
LangFuse observability for Knowledge Distiller — Tier 3 #14 + Tier 0d-5.

Graceful no-op when LANGFUSE_* env vars are missing — tests + dev loops that
don't need telemetry stay fast.

LangFuse v4 (2026 GA) API:
  - `from langfuse.langchain import CallbackHandler`
  - Client auto-configures from LANGFUSE_HOST / LANGFUSE_PUBLIC_KEY /
    LANGFUSE_SECRET_KEY env vars (no per-handler kwargs)
  - Per-invocation metadata + tags flow through LangChain's `config={...}`
    dict, not through handler constructor

Callers get:
  - `build_langfuse_handler()` → CallbackHandler or None
  - `langfuse_config(metadata, tags)` → `{"callbacks": [h], "metadata": ..., "tags": ...}`
    or empty dict when disabled — pass straight into `chain.ainvoke(..., config=...)`
"""
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


_DEFAULT_HOST = "http://langfuse-web.langfuse.svc.cluster.local:3000"


def langfuse_enabled() -> bool:
    """Cheap env-only probe — no network call, no import."""
    return bool(
        os.environ.get("LANGFUSE_PUBLIC_KEY", "").strip()
        and os.environ.get("LANGFUSE_SECRET_KEY", "").strip()
    )


def build_langfuse_handler() -> Optional[object]:
    """
    Return a langfuse.langchain.CallbackHandler when env is configured,
    else None. Handler reads LANGFUSE_HOST / LANGFUSE_PUBLIC_KEY /
    LANGFUSE_SECRET_KEY from env automatically (v4 behavior).

    Import is lazy so missing dep / missing env is silent and cheap.
    """
    if not langfuse_enabled():
        return None
    # Default host fallback for in-cluster access when LANGFUSE_HOST
    # isn't explicitly set
    if not os.environ.get("LANGFUSE_HOST", "").strip():
        os.environ["LANGFUSE_HOST"] = _DEFAULT_HOST
    try:
        from langfuse.langchain import CallbackHandler
        return CallbackHandler()
    except Exception as e:  # pragma: no cover — dep or init failure
        logger.warning(
            f"[langfuse] CallbackHandler init failed ({e}); telemetry disabled"
        )
        return None


def langfuse_config(
    *,
    metadata: dict | None = None,
    tags: list[str] | None = None,
) -> dict:
    """
    Build a LangChain `config` dict carrying the CallbackHandler + metadata
    + tags. Returns an empty dict when Langfuse is disabled — callers can
    splat it with `chain.ainvoke(inputs, config=langfuse_config(...) or None)`.

    LangFuse v4 reads `metadata` and `tags` from the LangChain config; they
    show up on the trace + every nested span.
    """
    handler = build_langfuse_handler()
    if handler is None:
        return {}
    cfg: dict = {"callbacks": [handler]}
    if metadata:
        cfg["metadata"] = metadata
    if tags:
        cfg["tags"] = tags
    return cfg
