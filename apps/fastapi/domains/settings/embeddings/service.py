"""Embedding endpoint adapter — independent connection from chat.

Thin OpenAI-compatible client over the user-configured external embedding
endpoint (Settings page "Embedding" card → `embedding_endpoint`
{url, model} + managed key `COELHO_EMBEDDING_API_KEY`). Same flexibility
as chat, kept as its own independent connection rather than tied to
chat's — embeddings can point at a different endpoint than chat.
"""
from __future__ import annotations
import domains
from . import domain, entities, errors, keys, params

import asyncio
import logging
import os
import time


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Endpoint resolution — same shape as chat's, separate settings key +
# credential so chat and embeddings can point at different endpoints.
# ---------------------------------------------------------------------------

def _env_first(*names: str, default: str = "") -> str:
    for name in names:
        v = (os.getenv(name) or "").strip()
        if v:
            return v
    return default


_ENV_URL = _env_first(*params.URL_ENVS)
_ENV_MODEL = _env_first(*params.MODEL_ENVS, default = params.DEFAULT_MODEL) or params.DEFAULT_MODEL

ENDPOINT = entities.EndpointConfig(
    base_url = domain.normalize_base_url(_ENV_URL),
    model    = _ENV_MODEL,
    api_key  = "dummy",
)

_endpoint_resolved_at = 0.0


def _resolve_endpoint(*, force_store: bool = False) -> entities.EndpointConfig:
    """Settings-page override applied over env (never raises)."""
    url, model = _ENV_URL, _ENV_MODEL
    key = ""
    try:
        store = domains.settings.credentials.service.get_store()
        ep = (store.read_settings(force=force_store) or {}).get(keys.SETTINGS_KEY)
        if isinstance(ep, dict):
            url = (ep.get("url") or "").strip() or url
            model = (ep.get("model") or "").strip() or model
        k = (domains.settings.credentials.service.resolve_key(keys.KEY_ENV) or "").strip()
        if k:
            key = k
    except Exception as e:
        logger.debug(f"[embedding-endpoint] endpoint resolve store miss: {e}")
    return entities.EndpointConfig(
        base_url = domain.normalize_base_url(url),
        model    = model or params.DEFAULT_MODEL,
        api_key  = key or "dummy",
    )


def _apply_endpoint(*, force: bool = False) -> bool:
    """Re-resolve + reassign the module global. Returns True if it changed."""
    global ENDPOINT
    global _endpoint_resolved_at
    now = time.monotonic()
    if not force and (now - _endpoint_resolved_at) < params.ENDPOINT_RESOLVE_TTL_S:
        return False
    _endpoint_resolved_at = now
    resolved = _resolve_endpoint(force_store=force)
    if resolved == ENDPOINT:
        return False
    ENDPOINT = resolved
    logger.info(
        f"[embedding-endpoint] endpoint updated → {resolved.base_url} model={resolved.model} "
        f"key={'set' if resolved.api_key != 'dummy' else 'none'}"
    )
    return True


def get_configured_model() -> str:
    """The currently-configured model pin — re-resolves the Settings store
    first (same TTL as every other read here) so a just-saved Settings-page
    change is picked up without waiting for the next embed call. Used by
    `domains.ycs.embedding_migration`'s gate check."""
    _apply_endpoint()
    return ENDPOINT.model


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
            import openai
        except Exception as e:
            raise errors.EmbeddingError(f"openai SDK not installed: {e}") from e
        client = openai.AsyncOpenAI(
            base_url=ENDPOINT.base_url,
            api_key=ENDPOINT.api_key,
            max_retries=0,
        )
        _CLIENT = client
        logger.info(f"[embedding-endpoint] AsyncOpenAI client → {ENDPOINT.base_url} model={ENDPOINT.model}")
        return client


def reset_embedding_client(*args, **kwargs) -> None:
    """Called after the Settings page saves a new embedding endpoint —
    re-resolves + drops the pooled client so the next call rebuilds
    against the new URL/key/model."""
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
    text: str = params.PROBE_TEXT,
    *,
    timeout_s: float = params.PROBE_TIMEOUT_S,
) -> tuple[list[float], dict]:
    """One real embeddings call against the configured embedding endpoint."""
    client = await _get_async_openai()
    kwargs: dict = {"model": ENDPOINT.model, "input": [text]}
    t0 = time.monotonic()
    try:
        with domains.settings.runtime.observability.spans.embedding_span(
            model = ENDPOINT.model, input_count = 1,
        ) as span:
            try:
                resp = await asyncio.wait_for(
                    client.embeddings.create(**kwargs), timeout=timeout_s,
                )
            except asyncio.TimeoutError as e:
                raise errors.EmbeddingTimeoutError(
                    f"embed_probe_async timed out after {timeout_s:.0f}s"
                ) from e
            latency_s = time.monotonic() - t0
            vector = list(resp.data[0].embedding) if resp.data else []
            model = getattr(resp, "model", None) or ENDPOINT.model
            domains.settings.runtime.observability.spans.record_embedding_response(
                span, model = model, vector_count = len(resp.data or []),
            )
    except errors.EmbeddingTimeoutError:
        domains.settings.runtime.observability.metrics.record_gen_ai_call(
            operation = "embedding", model = ENDPOINT.model, outcome = "timeout",
            duration_s = time.monotonic() - t0,
        )
        raise
    except Exception:
        domains.settings.runtime.observability.metrics.record_gen_ai_call(
            operation = "embedding", model = ENDPOINT.model, outcome = "error",
            duration_s = time.monotonic() - t0,
        )
        raise

    domains.settings.runtime.observability.metrics.record_gen_ai_call(
        operation = "embedding", model = model, outcome = "ok", duration_s = latency_s,
    )

    meta = {
        "model": model,
        "deployment": model,  # compat alias — YCS records `deployment`
        "dimensions": len(vector),
        "latency_s": round(latency_s, 3),
    }
    return vector, meta


async def embed_texts_async(
    texts: list[str],
) -> tuple[list[list[float]], str]:
    """Real batch embeddings call for actual callers (YCS, RR). Raises on
    failure; callers decide how to react.

    Returns `(vectors, model)` — `model` is the model that ACTUALLY served
    this call (from the response), which callers that persist vectors
    (YCS's Qdrant payloads) need to detect a model change."""
    if not texts:
        return [], ENDPOINT.model
    client = await _get_async_openai()
    kwargs: dict = {"model": ENDPOINT.model, "input": texts}
    t0 = time.monotonic()
    try:
        with domains.settings.runtime.observability.spans.embedding_span(
            model = ENDPOINT.model, input_count = len(texts),
        ) as span:
            try:
                resp = await client.embeddings.create(**kwargs)
            except Exception as e:
                raise errors.EmbeddingError(f"{type(e).__name__}: {e}") from e
            data = sorted(resp.data, key=lambda d: d.index)
            model = getattr(resp, "model", None) or ENDPOINT.model
            domains.settings.runtime.observability.spans.record_embedding_response(
                span, model = model, vector_count = len(data),
            )
    except Exception:
        domains.settings.runtime.observability.metrics.record_gen_ai_call(
            operation = "embedding", model = ENDPOINT.model, outcome = "error",
            duration_s = time.monotonic() - t0,
        )
        raise

    domains.settings.runtime.observability.metrics.record_gen_ai_call(
        operation = "embedding", model = model, outcome = "ok",
        duration_s = time.monotonic() - t0,
    )
    return [list(d.embedding) for d in data], model
