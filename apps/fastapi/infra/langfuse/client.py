"""Lazy LangFuse SDK client — singleton, env-driven, fail-soft.

`get_client()` returns the same `Langfuse` instance on every call, or None
if the package is missing or credentials aren't configured. Never raises.

Env vars (in priority order):
  LANGFUSE_HOST          base URL of the LangFuse instance (e.g.
                         http://langfuse-web.langfuse.svc.cluster.local:3000)
  LANGFUSE_PUBLIC_KEY    project public key (HTTP Basic user)
  LANGFUSE_SECRET_KEY    project secret key (HTTP Basic password)

If `LANGFUSE_HOST` is unset, falls back to deriving it from
`LANGFUSE_OTLP_ENDPOINT` (the OTLP exporter env var) by stripping
`/api/public/otel*` from the end — so a single env var configures both
the OTLP exporter and the SDK.
"""
from __future__ import annotations

import logging
import os
import re
import threading


logger = logging.getLogger(__name__)


_client = None
_init_lock = threading.Lock()
_init_attempted = False


def _resolve_host() -> str | None:
    host = os.environ.get("LANGFUSE_HOST")
    if host:
        return host.rstrip("/")
    otlp = os.environ.get("LANGFUSE_OTLP_ENDPOINT")
    if otlp:
        return re.sub(r"/api/public/otel.*$", "", otlp.rstrip("/"))
    return None


def is_available() -> bool:
    """True iff host + both credentials are present (does not import the SDK)."""
    return bool(
        _resolve_host()
        and os.environ.get("LANGFUSE_PUBLIC_KEY")
        and os.environ.get("LANGFUSE_SECRET_KEY")
    )


def get_client():
    """Return the singleton LangFuse client, or None if init failed.

    Idempotent: a failed init is remembered (returns None on every subsequent
    call within the process — no retries) so we don't pay the import + auth
    cost on every hot-path call."""
    global _client, _init_attempted
    if _client is not None:
        return _client
    if _init_attempted:
        return None
    with _init_lock:
        if _client is not None or _init_attempted:
            return _client
        _init_attempted = True
        host = _resolve_host()
        pk = os.environ.get("LANGFUSE_PUBLIC_KEY")
        sk = os.environ.get("LANGFUSE_SECRET_KEY")
        if not (host and pk and sk):
            logger.info(
                "[langfuse] SDK init skipped — host/public_key/secret_key not "
                "all set (host=%s pk_set=%s sk_set=%s)",
                bool(host), bool(pk), bool(sk),
            )
            return None
        try:
            from langfuse import Langfuse
        except Exception as e:
            logger.warning(
                f"[langfuse] SDK import failed ({type(e).__name__}: {e}) — "
                "SDK features disabled; OTLP trace ingestion still active"
            )
            return None
        try:
            _client = Langfuse(public_key=pk, secret_key=sk, host=host)
            logger.info(f"[langfuse] SDK client initialized → {host}")
            return _client
        except Exception as e:
            logger.warning(
                f"[langfuse] SDK client init failed "
                f"({type(e).__name__}: {e})"
            )
            return None
