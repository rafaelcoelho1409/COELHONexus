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
  - `build_langfuse_handler()` → singleton CallbackHandler or None
  - `langfuse_config(metadata, tags)` → `{"callbacks": [h], "metadata": ..., "tags": ...}`
    or empty dict when disabled — pass straight into `chain.ainvoke(..., config=...)`
  - `flush_langfuse()` → force-flush queued events (use after each LangGraph node)
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


# OP-44 hardening (2026-04-25 post-Run-13/14) — singleton handler + explicit
# flush. The previous per-call CallbackHandler() instantiation worked but the
# LangFuse SDK's auto-flush only triggers at process exit OR the threshold
# (default ~100 events). In a long-running Celery worker, traces can stay
# queued for an entire study before becoming visible in the UI. Explicit
# flush at every LangGraph node + Celery after_return hook + background
# 15s flush thread = three independent paths to UI visibility.
_HANDLER_SINGLETON: Optional[object] = None
_LANGFUSE_CLIENT_SINGLETON: Optional[object] = None
_INIT_LOGGED = False
_FLUSH_INTERVAL_SECONDS = 15
_flush_thread_started = False


def _force_init_log() -> None:
    """One-time startup log showing project/org/host so users can verify
    they're looking at the right project in the UI."""
    global _INIT_LOGGED
    if _INIT_LOGGED:
        return
    _INIT_LOGGED = True
    try:
        from langfuse import Langfuse
        client = Langfuse()
        ok = client.auth_check()
        host = os.environ.get("LANGFUSE_HOST", "(unset)")
        pk = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
        pk_masked = (pk[:10] + "..." + pk[-6:]) if len(pk) > 20 else "(short/unset)"
        logger.info(
            f"[langfuse] init OK — host={host} public_key={pk_masked} "
            f"auth_check={ok}"
        )
    except Exception as e:
        logger.warning(f"[langfuse] init log failed: {e}")


def _get_client():
    """Return the singleton Langfuse client (lazy init)."""
    global _LANGFUSE_CLIENT_SINGLETON
    if _LANGFUSE_CLIENT_SINGLETON is None:
        from langfuse import Langfuse
        _LANGFUSE_CLIENT_SINGLETON = Langfuse()
    return _LANGFUSE_CLIENT_SINGLETON


def _ensure_flush_thread() -> None:
    """Start the background flush thread once per process (idempotent)."""
    global _flush_thread_started
    if _flush_thread_started:
        return
    import threading, time
    def _flush_loop() -> None:
        while True:
            time.sleep(_FLUSH_INTERVAL_SECONDS)
            try:
                _get_client().flush()
            except Exception:
                # Background telemetry failure must never crash worker
                pass
    t = threading.Thread(
        target = _flush_loop,
        name = "langfuse-flush",
        daemon = True,  # don't block Celery shutdown
    )
    t.start()
    _flush_thread_started = True
    logger.info(
        f"[langfuse] background flush thread started "
        f"(interval={_FLUSH_INTERVAL_SECONDS}s)"
    )


def build_langfuse_handler() -> Optional[object]:
    """
    Return the SINGLETON langfuse.langchain.CallbackHandler when env is
    configured, else None. Handler reads LANGFUSE_HOST / LANGFUSE_PUBLIC_KEY
    / LANGFUSE_SECRET_KEY from env automatically (v4 behavior).

    On first successful build:
      - Logs project/host/auth-check status (for UI-project verification)
      - Starts background flush thread (15s interval)

    Singleton avoids per-call handler instantiation overhead and ensures
    LangFuse's internal trace-context tracking works across calls.
    """
    global _HANDLER_SINGLETON
    if _HANDLER_SINGLETON is not None:
        return _HANDLER_SINGLETON
    if not langfuse_enabled():
        return None
    # Default host fallback for in-cluster access when LANGFUSE_HOST
    # isn't explicitly set
    if not os.environ.get("LANGFUSE_HOST", "").strip():
        os.environ["LANGFUSE_HOST"] = _DEFAULT_HOST
    try:
        from langfuse.langchain import CallbackHandler
        handler = CallbackHandler()
        _HANDLER_SINGLETON = handler
        # Log init status + start flush thread on first success.
        # Defensive: any failure here must never block telemetry capture.
        try:
            _force_init_log()
            _ensure_flush_thread()
        except Exception as _e:
            logger.warning(f"[langfuse] post-init setup failed: {_e}")
        return handler
    except Exception as e:  # pragma: no cover — dep or init failure
        logger.warning(
            f"[langfuse] CallbackHandler init failed ({e}); telemetry disabled"
        )
        return None


def langfuse_config(
    *,
    metadata: dict | None = None,
    tags: list[str] | None = None,
    session_id: str | None = None,
    user_id: str | None = None,
    run_name: str | None = None,
) -> dict:
    """
    Build a LangChain `config` dict carrying the CallbackHandler + metadata
    + tags + session/user/run_name binding. Returns an empty dict when
    Langfuse is disabled — callers can splat it with
    `chain.ainvoke(inputs, config=langfuse_config(...) or None)`.

    LangFuse v4 reads:
      - `metadata` and `tags` from the LangChain config (shown on trace +
        every nested span)
      - `metadata.langfuse_session_id` → groups all traces under one study
      - `metadata.langfuse_user_id` → per-user cost rollups
      - `run_name` (LangChain top-level) → trace name in UI

    OP-LF-SESSION-ID / OP-LF-USER-ID / OP-LF-RUN-NAME (2026-04-25,
    post-Run-16): three new args. `session_id` and `user_id` get auto-
    injected into `metadata` under the LangFuse-recognized keys without
    callers having to remember the prefix. `run_name` becomes the
    searchable trace name (e.g. "kd-planner-shard-04" instead of the
    default "RunnableSequence").
    """
    handler = build_langfuse_handler()
    if handler is None:
        return {}
    cfg: dict = {"callbacks": [handler]}
    md = dict(metadata) if metadata else {}
    if session_id:
        md["langfuse_session_id"] = str(session_id)
    if user_id:
        md["langfuse_user_id"] = str(user_id)
    if md:
        cfg["metadata"] = md
    if tags:
        cfg["tags"] = tags
    if run_name:
        cfg["run_name"] = run_name
    return cfg


def flush_langfuse(reason: str = "explicit") -> None:
    """
    Force-flush all queued LangFuse events to the server immediately.
    Use after each LangGraph node + Celery after_return hook for
    real-time UI visibility.

    Non-raising: failures are logged at WARN, never crash callers.
    The whole point of this function is "best effort delivery"; if the
    LangFuse server is unreachable, telemetry must never block the
    pipeline.

    Optional env switch: `LANGFUSE_FLUSH_VERBOSE=1` → log every flush.
    """
    if not langfuse_enabled():
        return
    try:
        _get_client().flush()
        if os.environ.get("LANGFUSE_FLUSH_VERBOSE", "").strip() == "1":
            logger.info(f"[langfuse] flushed (reason={reason})")
    except Exception as e:
        logger.warning(f"[langfuse] flush failed (reason={reason}): {e}")


def emit_failure_event(
    *,
    chapter_number: int,
    failure_mode: str,
    framework: str | None = None,
    iteration: int | None = None,
    extra: dict | None = None,
) -> None:
    """
    OP-LF-FAILURE-TAGS (2026-04-25 post-Run-16) — emit a standalone LangFuse
    event tagged with the failure mode at the moment a synth chapter falls
    into a non-happy-path commit (OP-7 early-stop, OP-12 best-seen rescue,
    OP-19 exception rescue, sentinel emission, accepted-below-threshold).

    These events are independent of the LangChain CallbackHandler trace tree
    — they show up as their own events in the LangFuse UI and can be filtered
    by tag (e.g. tag=op19-rescue, tag=ch04). Used to count failure rates per
    chapter / per failure-mode without log-scraping.

    Non-raising: telemetry must never crash the pipeline.

    `failure_mode` should be one of:
      - "op7-early-stop"    — audit-regression early-stop (OP-7)
      - "op12-best-seen"    — no graded iter, best audit-failed iter committed (OP-12)
      - "op19-rescue"       — synth exception bypassed; best_audit_iter committed (OP-19)
      - "sentinel"          — terminal failure, sentinel emitted for siblings
      - "debt-commit"       — accepted below acceptance_threshold, DEBT flag set
      - "curator-skip"      — curator skipped (vault collision or preservation fail)
    """
    if not langfuse_enabled():
        return
    try:
        client = _get_client()
        metadata = {"chapter_number": str(chapter_number)}
        if framework is not None:
            metadata["framework"] = framework
        if iteration is not None:
            metadata["iteration"] = str(iteration)
        if extra:
            for k, v in extra.items():
                metadata[k] = str(v) if not isinstance(v, str) else v
        tags = [f"ch{chapter_number:02d}", failure_mode, "failure-event"]
        # Use start_observation if available (v3+), else fallback to span-then-end
        try:
            obs = client.start_observation(
                name = f"failure:{failure_mode}:ch{chapter_number:02d}",
                as_type = "event",
                metadata = metadata,
            )
            obs.update(tags = tags)
            obs.end()
        except Exception:
            # Fallback: span-and-end if start_observation isn't on this SDK ver
            with client.start_as_current_span(
                name = f"failure:{failure_mode}:ch{chapter_number:02d}",
                metadata = metadata,
            ) as span:
                span.update_trace(tags = tags)
    except Exception as e:
        logger.warning(
            f"[langfuse] emit_failure_event(ch{chapter_number:02d}, "
            f"{failure_mode}) failed: {e}"
        )


def probe_langfuse() -> dict:
    """
    Connectivity + auth probe. Use at startup or via a debug endpoint.
    Returns a dict with keys: enabled, host, public_key_prefix, auth_ok,
    error (if any).
    """
    pk = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
    result = {
        "enabled": langfuse_enabled(),
        "host": os.environ.get("LANGFUSE_HOST", "(unset)"),
        "public_key_prefix": pk[:10] if pk else "(unset)",
        "auth_ok": False,
        "error": None,
    }
    if not result["enabled"]:
        result["error"] = "LANGFUSE_PUBLIC_KEY or LANGFUSE_SECRET_KEY missing"
        return result
    try:
        from langfuse import Langfuse
        result["auth_ok"] = Langfuse().auth_check()
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
    return result
