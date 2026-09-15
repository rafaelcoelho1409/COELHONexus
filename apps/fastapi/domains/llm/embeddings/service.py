"""Embedding endpoint adapter — independent connection from chat.

Mirrors `domains/llm/rotator/chain/service.py`'s endpoint-resolution +
pooled-client pattern exactly, but for a SEPARATE endpoint: embeddings can
point at COELHO LLM Rotator (its Embedding Curator resolves the actual
model) or any other OpenAI-compatible embedding service — same flexibility
the LLM Endpoint card already has for chat, kept as its own independent
connection rather than reusing chat's. Settings-page-configured via
`api/v1/llm/settings/router.py`'s `/embedding` routes.

2026-09-12: split out from chain/service.py after the first version wrongly
rode on chat's connection — the user wants embeddings independently
pointable at a different endpoint than chat, not tied to it.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Endpoint resolution — same shape as chain/service.py's, separate settings
# key + credential so chat and embeddings can point at different endpoints.
# ---------------------------------------------------------------------------

def _normalize_base_url(url: str) -> str:
    u = (url or "").strip().rstrip("/")
    if not u:
        return _DEFAULT_EMBEDDING_URL
    for suffix in ("/embeddings", "/embeddings/"):
        if u.endswith(suffix):
            u = u[: -len(suffix)].rstrip("/")
    if u.endswith("/v1") or u.endswith("/api/v1/llm/openai/v1"):
        return u
    if "://" in u and u.count("/") == 2:  # e.g. http://host:8000
        return u + "/api/v1/llm/openai/v1"
    return u


# Same default address as chat's — the rotator is the primary use case
# today, but this is an independently-overridable connection, not a
# reference to chat's own resolved URL.
_DEFAULT_EMBEDDING_URL = "http://coelho-llm-rotator-fastapi.coelho-llm-rotator-dev.svc.cluster.local:8000/api/v1/llm/openai/v1"

_ENV_EMBEDDING_URL = os.getenv("COELHO_EMBEDDING_URL", _DEFAULT_EMBEDDING_URL)
_ENV_EMBEDDING_MODEL = os.getenv("COELHO_EMBEDDING_MODEL", "auto").strip() or "auto"
_ENV_API_KEY = os.getenv("COELHO_EMBEDDING_API_KEY", "").strip()

_ENDPOINT_RESOLVE_TTL_S = 10.0
_endpoint_resolved_at = 0.0

COELHO_EMBEDDING_URL = _normalize_base_url(_ENV_EMBEDDING_URL)
COELHO_EMBEDDING_MODEL = _ENV_EMBEDDING_MODEL
COELHO_EMBEDDING_API_KEY = _ENV_API_KEY or "dummy"


def _resolve_endpoint(*, force_store: bool = False) -> tuple[str, str, str]:
    """(base_url, api_key, model) with Settings-page override applied."""
    url, model, key = _ENV_EMBEDDING_URL, _ENV_EMBEDDING_MODEL, _ENV_API_KEY
    try:
        from domains.llm.credentials import get_store, resolve_key

        ep = (get_store().read_settings(force=force_store) or {}).get("embedding_endpoint")
        if isinstance(ep, dict):
            url = (ep.get("url") or "").strip() or url
            model = (ep.get("model") or "").strip() or model
        k = (resolve_key("COELHO_EMBEDDING_API_KEY") or "").strip()
        if k:
            key = k
    except Exception as e:
        logger.debug(f"[embedding-adapter] endpoint resolve store miss: {e}")
    return _normalize_base_url(url), (key or "dummy"), (model or "auto")


def _apply_endpoint(*, force: bool = False) -> bool:
    global COELHO_EMBEDDING_URL, COELHO_EMBEDDING_API_KEY, COELHO_EMBEDDING_MODEL
    global _endpoint_resolved_at
    now = time.monotonic()
    if not force and (now - _endpoint_resolved_at) < _ENDPOINT_RESOLVE_TTL_S:
        return False
    _endpoint_resolved_at = now
    url, key, model = _resolve_endpoint(force_store=force)
    if (url, key, model) == (COELHO_EMBEDDING_URL, COELHO_EMBEDDING_API_KEY, COELHO_EMBEDDING_MODEL):
        return False
    COELHO_EMBEDDING_URL, COELHO_EMBEDDING_API_KEY, COELHO_EMBEDDING_MODEL = url, key, model
    logger.info(
        f"[embedding-adapter] endpoint updated → {url} model={model} "
        f"key={'set' if key != 'dummy' else 'none'}"
    )
    return True


def get_configured_model() -> str:
    """The currently-configured pin (`"{provider}/{model}"`, or literally
    `"auto"`) — re-resolves the Settings store first (same TTL as every
    other read here) so a just-saved Settings-page change is picked up
    without waiting for the next embed call. Used by
    `domains.ycs.embedding_migration`'s gate check."""
    _apply_endpoint()
    return COELHO_EMBEDDING_MODEL


_apply_endpoint(force=True)


# ---------------------------------------------------------------------------
# Pooled AsyncOpenAI singleton — separate from chat's pooled client
# ---------------------------------------------------------------------------

_CLIENT: object | None = None
_CLIENT_LOCK = asyncio.Lock()


async def _get_async_openai():
    global _CLIENT
    if _apply_endpoint() and _CLIENT is not None:
        stale, _CLIENT = _CLIENT, None
        try:
            await stale.close()
        except Exception:
            pass
    if _CLIENT is not None:
        return _CLIENT
    async with _CLIENT_LOCK:
        if _CLIENT is not None:
            return _CLIENT
        try:
            import openai as _openai
        except Exception as e:
            raise RuntimeError(f"openai SDK not installed: {e}") from e
        client = _openai.AsyncOpenAI(
            base_url=COELHO_EMBEDDING_URL,
            api_key=COELHO_EMBEDDING_API_KEY,
            max_retries=0,
        )
        _CLIENT = client
        logger.info(f"[embedding-adapter] AsyncOpenAI client → {COELHO_EMBEDDING_URL} model={COELHO_EMBEDDING_MODEL}")
        return client


def reset_embedding_client(*args, **kwargs) -> None:
    """Called after the Settings page saves a new embedding endpoint —
    re-resolves + drops the pooled client so the next call rebuilds
    against the new URL/key/model. Mirrors chain/service.py::reset_rotator."""
    global _CLIENT
    try:
        _apply_endpoint(force=True)
    except Exception:
        pass
    try:
        if _CLIENT is not None:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(_CLIENT.close())
            except RuntimeError:
                pass
        _CLIENT = None
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Probe — powers the Settings page's "Test" button
# ---------------------------------------------------------------------------

async def embed_probe_async(
    text: str = "connection test",
    *,
    languages: list[str] | None = None,
    timeout_s: float = 20.0,
) -> tuple[list[float], dict]:
    """One real embeddings call against the configured embedding endpoint.

    `languages` is accepted for internal/programmatic callers only (not a
    Settings-page field — YCS ingests multiple languages per run, so no
    single language preference makes sense there) — forwarded as
    `metadata.languages` when supplied, the same opaque-metadata convention
    chat's `rotator_task` uses. The rotator's Embedding Curator reads it as
    a ranked hint; any other endpoint just ignores it."""
    client = await _get_async_openai()
    kwargs: dict = {"model": COELHO_EMBEDDING_MODEL, "input": [text]}
    if languages:
        kwargs["extra_body"] = {"metadata": {"languages": languages}}
    t0 = time.monotonic()
    try:
        resp = await asyncio.wait_for(
            client.embeddings.create(**kwargs), timeout=timeout_s,
        )
    except asyncio.TimeoutError as e:
        raise TimeoutError(
            f"embed_probe_async timed out after {timeout_s:.0f}s"
        ) from e
    latency_s = time.monotonic() - t0
    vector = list(resp.data[0].embedding) if resp.data else []
    meta = {
        "deployment": getattr(resp, "model", None) or COELHO_EMBEDDING_MODEL,
        "dimensions": len(vector),
        "latency_s": round(latency_s, 3),
    }
    return vector, meta


async def embed_texts_async(
    texts: list[str], *, languages: list[str] | None = None,
) -> tuple[list[list[float]], str]:
    """Real batch embeddings call for actual callers (YCS). Raises on
    failure; callers decide how to react.

    2026-09-15: now returns `(vectors, model)` — was vectors-only, which
    silently discarded `resp.model` (the model that ACTUALLY served this
    call) on every single real embed, even though the rotator has always
    put it in the response. Callers that persist vectors (YCS's Qdrant
    payloads) need this to detect a model change, not just the one-off
    Settings-page probe (`embed_probe_async`, unchanged below)."""
    if not texts:
        return [], COELHO_EMBEDDING_MODEL
    client = await _get_async_openai()
    kwargs: dict = {"model": COELHO_EMBEDDING_MODEL, "input": texts}
    if languages:
        kwargs["extra_body"] = {"metadata": {"languages": languages}}
    resp = await client.embeddings.create(**kwargs)
    data = sorted(resp.data, key=lambda d: d.index)
    model = getattr(resp, "model", None) or COELHO_EMBEDDING_MODEL
    return [list(d.embedding) for d in data], model


# ---------------------------------------------------------------------------
# Embedding Curator advisory surface (COELHO LLM Rotator only) — the
# rotator's `/v1/embeddings` no longer auto-follows this surface (2026-09-15,
# see that endpoint's docstring); it's now purely informational, for a human
# (Settings page "browse models" button) or the embedding-migration gate to
# decide WHEN to move the pin. Best-effort: any other OpenAI-compatible
# endpoint the user points embeddings at won't have these routes at all —
# every function here returns None rather than raising on failure.
# ---------------------------------------------------------------------------

_ROTATOR_OPENAI_SUFFIX = "/api/v1/llm/openai/v1"


def _rotator_origin() -> str | None:
    """Best-guess rotator origin (scheme+host+port) derived from the
    configured embedding base_url. Only valid when that url actually
    points at COELHO LLM Rotator (the normal case, but not guaranteed —
    the Embedding endpoint card accepts any OpenAI-compatible service)."""
    url = COELHO_EMBEDDING_URL
    if url.endswith(_ROTATOR_OPENAI_SUFFIX):
        return url[: -len(_ROTATOR_OPENAI_SUFFIX)]
    return None


async def fetch_rotator_recommendation(
    *, languages: list[str] | None = None, timeout_s: float = 10.0,
) -> dict | None:
    """`GET /api/v1/embeddings/recommend` — the shared current pick +
    full ranking. None if the configured endpoint isn't the rotator, or
    the call fails for any reason (never raises — this is advisory)."""
    origin = _rotator_origin()
    if origin is None:
        return None
    try:
        import httpx
        params = {"languages": ",".join(languages)} if languages else {}
        async with httpx.AsyncClient(timeout = timeout_s) as client:
            resp = await client.get(f"{origin}/api/v1/embeddings/recommend", params = params)
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.debug(f"[embedding-adapter] recommend fetch failed: {e}")
        return None


async def fetch_rotator_candidates(*, timeout_s: float = 10.0) -> list[dict] | None:
    """`GET /api/v1/embeddings/candidates` — live discovery snapshot of
    every embedding-looking model the rotator currently sees across
    connected providers. None on failure (see `fetch_rotator_recommendation`)."""
    origin = _rotator_origin()
    if origin is None:
        return None
    try:
        import httpx
        async with httpx.AsyncClient(timeout = timeout_s) as client:
            resp = await client.get(f"{origin}/api/v1/embeddings/candidates")
            resp.raise_for_status()
            return (resp.json() or {}).get("candidates") or []
    except Exception as e:
        logger.debug(f"[embedding-adapter] candidates fetch failed: {e}")
        return None
