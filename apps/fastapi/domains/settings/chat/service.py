"""Chat endpoint adapter — the ONLY connection Nexus makes to an external LLM.

Thin OpenAI-compatible client over the user-configured external endpoint
(Settings page "LLM Endpoint" card → `llm_endpoint` {url, model} + managed
key `COELHO_LLM_API_KEY`). No gateway logic here: no arm pool, no bandit
cells, no cascade, no feedback loop — the endpoint owns all of that.
Every caller (DD planner/synth, YCS Ask/query/graph, RR orchestrator/
subagents, Langfuse judges) builds through `build_chat_model()` or calls
`chat_text_async()` directly.

SOTA Sept 2026 optimizations (kept from the previous adapter):
- Singleton AsyncOpenAI with pooled httpx.AsyncClient (Limits 200/100,
  http2, keepalive 30s) → reuses TCP connections across 135+ corpus
  calls, avoids per-call client construction (~15 ms + TLS overhead).
- Raw OpenAI SDK path instead of LangChain ChatOpenAI wrapper for the
  hot loop → cuts ~20-30 ms of message conversion per call.
- MinIO I/O decoupled from the LLM semaphore (see doc_distill service)
  — the semaphore gates only the network-bound LLM hop.
"""
from __future__ import annotations
import domains
from . import domain, entities, errors, keys, params

import asyncio
import logging
import os
import re
import time

import httpx
from langchain_openai import ChatOpenAI


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Endpoint resolution — Settings-page override wins, env is the fallback.
# `ENDPOINT` is the *currently resolved* value; _apply_endpoint()
# re-resolves + reassigns it (throttled, or forced from
# reset_chat_client()). A store outage degrades to env and never raises.
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
    except Exception as e:  # store/import miss → env + default
        logger.debug(f"[chat-endpoint] endpoint resolve store miss: {e}")
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
        f"[chat-endpoint] endpoint updated → {resolved.base_url} model={resolved.model} "
        f"key={'set' if resolved.api_key != 'dummy' else 'none'}"
    )
    return True


_apply_endpoint(force=True)


# ---------------------------------------------------------------------------
# Pooled AsyncOpenAI singleton
# ---------------------------------------------------------------------------

_CLIENT: object | None = None
_CLIENT_LOCK = asyncio.Lock()

_POOL_KWARGS = {
    "max_connections": params.POOL_MAX_CONNECTIONS,
    "max_keepalive_connections": params.POOL_MAX_KEEPALIVE,
    "keepalive_expiry_s": params.POOL_KEEPALIVE_EXPIRY_S,
}
_TIMEOUT_KWARGS = {
    "default_s": params.CLIENT_MAX_TIMEOUT_S,
    "connect_s": params.CONNECT_TIMEOUT_S,
    "write_s": params.WRITE_TIMEOUT_S,
    "pool_s": params.POOL_TIMEOUT_S,
}

async def _get_async_openai():
    """Singleton AsyncOpenAI with pooled httpx client (lazy, thread-safe for async)."""
    global _CLIENT
    # Pick up a Settings-page endpoint change (throttled store re-read). A
    # process that didn't call reset_chat_client() itself (e.g. the celery
    # worker when the change was made from fastapi) converges within
    # ENDPOINT_RESOLVE_TTL_S.
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
            raise errors.ChatError(f"openai SDK not installed: {e}") from e

        # Use a shared AsyncClient with pooling; http2 multiplexing if h2
        # is installed, http/1.1 keep-alive fallback otherwise.
        try:
            http_client = httpx.AsyncClient(
                limits=domain.build_pool_limits(**_POOL_KWARGS),
                http2=True,
                timeout=domain.build_client_timeout(None, **_TIMEOUT_KWARGS),
                follow_redirects=True,
            )
            http2_enabled = True
        except ImportError:
            http_client = httpx.AsyncClient(
                limits=domain.build_pool_limits(**_POOL_KWARGS),
                http2=False,
                timeout=domain.build_client_timeout(None, **_TIMEOUT_KWARGS),
                follow_redirects=True,
            )
            http2_enabled = False
        client = openai.AsyncOpenAI(
            base_url=ENDPOINT.base_url,
            api_key=ENDPOINT.api_key,
            max_retries=0,  # the endpoint owns retries; SDK retries would stack a redundant loop
            http_client=http_client,
        )
        _CLIENT = client
        logger.info(f"[chat-endpoint] AsyncOpenAI pooled client → {ENDPOINT.base_url} model={ENDPOINT.model} (http2={http2_enabled})")
        return client


# ---------------------------------------------------------------------------
# Helpers — ChatOpenAI via endpoint (LangChain callers)
# ---------------------------------------------------------------------------

_SHARED_HTTP_CLIENT: object | None = None


def _get_shared_http_client():
    """Module-level pooled `httpx.AsyncClient` shared by every `ChatOpenAI`
    built below (lazily created, no lock — worst case two racing callers
    each build one and one wins; both are functionally identical pools).

    The pool is transport-agnostic (per-request base_url comes from the
    SDK client, not this transport), so a Settings-page endpoint move
    needs no rebuild here — only `_get_async_openai`'s bound client
    needs the reset treatment."""
    global _SHARED_HTTP_CLIENT
    if _SHARED_HTTP_CLIENT is not None:
        return _SHARED_HTTP_CLIENT
    try:
        client = httpx.AsyncClient(
            limits=domain.build_pool_limits(**_POOL_KWARGS),
            http2=True,
            timeout=domain.build_client_timeout(None, **_TIMEOUT_KWARGS),
            follow_redirects=True,
        )
    except ImportError:
        client = httpx.AsyncClient(
            limits=domain.build_pool_limits(**_POOL_KWARGS),
            http2=False,
            timeout=domain.build_client_timeout(None, **_TIMEOUT_KWARGS),
            follow_redirects=True,
        )
    _SHARED_HTTP_CLIENT = client
    return client


def build_chat_model(
    *,
    timeout_s:       float | None = None,
    max_tokens:      int | None   = None,
    temperature:     float | None = None,
    response_format: dict | None  = None,
    max_retries:     int          = 0,
):
    """Shared LangChain `ChatOpenAI` over the configured endpoint — one
    place to keep `max_retries=0` applied consistently BY DEFAULT. The
    endpoint handles its own retries; an SDK-level retry loop on top of
    it would stack a second, redundant retry for callers that already
    retry themselves.

    `max_retries` is overridable (still defaults to 0 — every existing
    caller is unaffected) for callers with NO retry protection of their
    own: RR's orchestrator/subagent models are passed directly to
    DeepAgents as the bare model object, so they can't use
    `resilient_ainvoke` (wrapping the model breaks DeepAgents'
    `isinstance(model, BaseChatModel)` check). SDK-level retry is the
    only retry mechanism that doesn't touch the model's class.

    Every instance shares ONE module-level pooled `httpx.AsyncClient`
    (same pool shape as the raw hot path's client). Per-request
    `timeout_s` still governs each call."""
    kwargs: dict = {
        "base_url":          ENDPOINT.base_url,
        "api_key":           ENDPOINT.api_key,
        "model":             ENDPOINT.model,
        "temperature":       temperature if temperature is not None else params.DEFAULT_TEMPERATURE,
        "max_retries":       max_retries,
        "http_async_client": _get_shared_http_client(),
    }
    if timeout_s is not None:
        kwargs["timeout"] = timeout_s
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    if response_format is not None:
        kwargs["model_kwargs"] = {"response_format": response_format}
    return ChatOpenAI(**kwargs)


# ------------------------------------------------------------------
# Chat — raw AsyncOpenAI pooled, no per-call ChatOpenAI construction
# ------------------------------------------------------------------

async def chat_judge_async(
    prompt: str,
    max_tokens: int = params.DEFAULT_MAX_TOKENS,
    temperature: float = params.DEFAULT_TEMPERATURE,
) -> str:
    text, _ = await chat_text_async(prompt, max_tokens=max_tokens, temperature=temperature)
    return text


async def chat_text_async(
    prompt: str,
    *,
    max_tokens: int = params.DEFAULT_MAX_TOKENS,
    temperature: float = params.DEFAULT_TEMPERATURE,
    timeout_s: float = params.DEFAULT_TIMEOUT_S,
    expected_pattern: str | None = None,
    response_format: dict | None = None,
) -> tuple[str, dict]:
    client = await _get_async_openai()

    # Build OpenAI-compatible kwargs — only send non-None to stay minimal
    kwargs: dict = {
        "model": ENDPOINT.model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "timeout": timeout_s,
    }
    if response_format is not None:
        # OpenAI expects {"type": "json_object"} or {"type": "json_schema", "json_schema": {...}}
        kwargs["response_format"] = response_format  # type: ignore

    # Remove None timeout entry if SDK disallows it as None
    if kwargs.get("timeout") is None:
        kwargs.pop("timeout", None)

    t0 = time.monotonic()
    # Hard wall-clock backstop. httpx's `read` timeout measures time between
    # chunks, not total call duration — a pooled HTTP/2 connection that goes
    # stale (NAT/LB silently drops idle connections) or trickles data can
    # outlive both the client-level and per-request httpx timeout without
    # ever raising. asyncio.wait_for enforces actual total duration instead.
    # Safe to rely on here because max_retries=0 above means there's no
    # internal SDK retry loop that could eat this budget out from under it.
    backstop_s = (timeout_s or params.DEFAULT_TIMEOUT_S) + params.BACKSTOP_MARGIN_S

    async def _do_call():
        return await client.chat.completions.create(**kwargs)  # type: ignore[arg-type]

    usage = None
    try:
        with domains.settings.runtime.observability.spans.chat_completion_span(
            model = ENDPOINT.model, temperature = temperature, max_tokens = max_tokens,
        ) as span:
            try:
                resp = await asyncio.wait_for(_do_call(), timeout = backstop_s)
            except asyncio.TimeoutError as e:
                raise errors.ChatTimeoutError(
                    f"chat_text_async hard backstop fired after "
                    f"{backstop_s:.0f}s (requested timeout_s={timeout_s})"
                ) from e
            except Exception as e:
                # Preserve message for upstream domain.classify_error
                raise errors.ChatError(f"{type(e).__name__}: {e}") from e

            latency_s = float(time.monotonic() - t0)

            # Extract text — OpenAI returns choices[0].message.content
            try:
                choice = resp.choices[0] if getattr(resp, "choices", None) else None
                msg = getattr(choice, "message", None) if choice else None
                text = (getattr(msg, "content", "") or "").strip() if msg else ""
                # Fallback for dict responses
                if not text and isinstance(resp, dict):
                    text = (((resp.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
            except Exception:
                text = ""

            # `resp.model` is whatever the endpoint reports as the serving model.
            model = ENDPOINT.model
            try:
                m = getattr(resp, "model", None)
                if isinstance(m, str) and m:
                    model = m
                elif isinstance(resp, dict) and resp.get("model"):
                    model = str(resp["model"])
            except Exception:
                pass

            usage = getattr(resp, "usage", None)
            domains.settings.runtime.observability.spans.record_chat_response(
                span,
                model         = model,
                input_tokens  = getattr(usage, "prompt_tokens", None) if usage is not None else None,
                output_tokens = getattr(usage, "completion_tokens", None) if usage is not None else None,
            )
    except errors.ChatTimeoutError:
        domains.settings.runtime.observability.metrics.record_gen_ai_call(
            operation = "chat", model = ENDPOINT.model, outcome = "timeout",
            duration_s = time.monotonic() - t0,
        )
        raise
    except Exception:
        domains.settings.runtime.observability.metrics.record_gen_ai_call(
            operation = "chat", model = ENDPOINT.model, outcome = "error",
            duration_s = time.monotonic() - t0,
        )
        raise

    domains.settings.runtime.observability.metrics.record_gen_ai_call(
        operation = "chat", model = model, outcome = "ok", duration_s = latency_s,
        input_tokens  = getattr(usage, "prompt_tokens", None) if usage is not None else None,
        output_tokens = getattr(usage, "completion_tokens", None) if usage is not None else None,
    )

    meta = {
        "model": model,
        "deployment": model,  # compat alias — DD/RR/YCS counter + log call sites read `deployment`
        "attempts": 1,
        "latency_s": round(latency_s, 3),
    }

    if expected_pattern:
        try:
            if not re.compile(expected_pattern).match(text.split()[0].strip(".,;:!\"'`") if text else ""):
                meta["schema_invalid"] = True
        except Exception:
            pass

    # Usage extraction for the DD per-run LLM counter (best-effort, never raises).
    # `usage` was already read off `resp` above, while enriching the gen_ai span.
    try:
        if usage is not None:
            class _Msg:
                content = text
                response_metadata = {"model_name": model}
                usage_metadata = {
                    "input_tokens": getattr(usage, "prompt_tokens", 0) or 0,
                    "output_tokens": getattr(usage, "completion_tokens", 0) or 0,
                }
            _bump_dd_llm_counter(_Msg(), model=model)
        else:
            class _Msg2:
                content = text
                response_metadata = {"model_name": model}
                usage_metadata = {}
            _bump_dd_llm_counter(_Msg2(), model=model)
    except Exception:
        pass

    return text, meta


def _bump_dd_llm_counter(response, model: str | None = None) -> dict | None:
    try:
        # Adapt to bump_current_call expected shape
        fake_resp = {
            "model": model or ENDPOINT.model,
            "usage": getattr(response, "usage_metadata", None) or {},
            "choices": [{"message": {"content": getattr(response, "content", "")}}],
        }
        return domains.dd.runtime.service.bump_current_call(
            response=fake_resp, deployment=model or ENDPOINT.model,
        )
    except Exception as e:
        logger.debug(f"[chat-endpoint] bump failed: {e}")
        return None


def reset_chat_client(*args, **kwargs) -> None:
    """Re-resolve the endpoint (Settings page just changed it) then drop the
    pooled client so the next call rebuilds against the new URL/key/model."""
    global _CLIENT
    try:
        _apply_endpoint(force=True)
    except Exception:
        pass
    try:
        if _CLIENT is not None:
            try:
                if hasattr(_CLIENT, "close"):
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(_CLIENT.close())  # type: ignore
                    except RuntimeError:
                        pass
            except Exception:
                pass
        _CLIENT = None
    except Exception:
        pass
    return None
