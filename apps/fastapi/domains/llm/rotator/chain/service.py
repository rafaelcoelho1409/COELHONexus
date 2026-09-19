"""COELHO LLM Rotator adapter — exclusive endpoint, no per-process weights.

All Planner/Synth calls now route through the universal free-quota gateway:
  standalone rotator (coelho-llm-rotator) via OpenAI-compatible HTTP. Always a
  separately-deployed service — Nexus never bundles or deploys one itself.
  Dev workflow: http://coelho-llm-rotator-fastapi.coelho-llm-rotator-dev.svc.cluster.local:8000/v1
  Tailnet:      https://coelho-llm-rotator.tail39dc94.ts.net/v1
  Legacy compat: /api/v1/llm/openai/v1 prefixed path is auto-normalized.

No dd_process / heavyweight / bandit weights here — FGTS-VA + TrueSkill + latency EWMA
lives inside the rotator itself (single auto pool, 21 models). This module is a thin
OpenAI-compat adapter preserving the old `chat_judge_*` signatures so Planner/Synth
require zero per-file churn. (2026-09-18: the `embed_via_router_*` pair this
docstring used to also mention was removed — it was local in-process FastEmbed,
not the external rotator its name implied; embeddings go through
`domains.llm.embeddings` now, the genuine Settings-page-configured endpoint.)

SOTA Sept 2026 optimizations:
- Singleton AsyncOpenAI with pooled httpx.AsyncClient (Limits 200/100, http2, keepalive 30s)
  → reuses TCP connections across 135+ corpus calls, avoids per-call ChatOpenAI construction
    that previously allocated a fresh httpx client (≈15 ms + TLS/handshake overhead).
- Raw OpenAI SDK path instead of LangChain ChatOpenAI wrapper for doc_distill hot loop
  → cuts ~20-30 ms of message conversion & response_metadata massaging per call.
- Connection pooling + http2 multiplexing lets 20-30 concurrent in-flight share the same
  TCP pool without pool exhaustion (see httpx Limits docs + Decodo 2026 benchmark).
- Rotator-side simple-shuffle + allowed_fails circuit breaker already absorbs 429s,
  so client concurrency can safely rise from 8 → 24 without the old 36% rate-limit blowup.
- MinIO I/O decoupled from LLM semaphore (see doc_distill service) — semaphore gates
  only the network-bound LLM hop, not the storage read.
"""
from __future__ import annotations

import asyncio
import functools
import logging
import os
import re
import time

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Endpoint resolution — normalize any legacy /api/v1/llm/openai/v1 prefix to /v1
# ---------------------------------------------------------------------------

def _normalize_base_url(url: str) -> str:
    """Return an OpenAI SDK base_url (scheme+host+prefix without /chat/completions).

    Accepts:
      - http://host:8000/v1
      - http://host:8000/api/v1/llm/openai/v1
      - http://host:8000/v1/chat/completions  → strips trailing segment
      - http://host:8000/           → appends /api/v1/llm/openai/v1
    """
    u = (url or "").strip().rstrip("/")
    if not u:
        # Must track _DEFAULT_ROTATOR_URL below (duplicated: this fn runs
        # before that constant is defined at module scope).
        return "http://coelho-llm-rotator-fastapi.coelho-llm-rotator-dev.svc.cluster.local:8000/api/v1/llm/openai/v1"
    # Strip known chat completions suffix if caller accidentally included it
    for suffix in ("/chat/completions", "/chat/completions/"):
        if u.endswith(suffix):
            u = u[: -len(suffix)].rstrip("/")
    # Already correct v1 prefixes — keep as-is
    if u.endswith("/v1") or u.endswith("/api/v1/llm/openai/v1"):
        return u
    # Bare host:port → append /api/v1/llm/openai/v1 (coelhonexus rotator)
    if "://" in u and u.count("/") == 2:  # e.g. http://host:8000
        return u + "/api/v1/llm/openai/v1"
    return u

# Endpoint resolution precedence: Settings-page override (credential store,
# key "llm_endpoint" + managed key "COELHO_LLM_API_KEY") → env var → default.
# The globals below are the *currently resolved* values, read by ~15 call
# sites at call time; _apply_endpoint() re-resolves + reassigns them
# (throttled, or forced from reset_rotator()). A store outage degrades to
# env/default and never raises.
#
# There is no bundled in-cluster rotator anymore (2026-09-11) — COELHO LLM
# Rotator is always a separately-deployed service (its own `skaffold dev`,
# its own chart, its own namespace). This default is just that service's
# usual address for the two-`skaffold dev` dev workflow; the Settings-page
# field overrides it for anything else (a different rotator, OpenAI, ...).
# Nexus never bundles or deploys a rotator of its own.
_DEFAULT_ROTATOR_URL = "http://coelho-llm-rotator-fastapi.coelho-llm-rotator-dev.svc.cluster.local:8000/api/v1/llm/openai/v1"

_ENV_ROTATOR_URL = os.getenv("COELHO_LLM_ROTATOR_URL", _DEFAULT_ROTATOR_URL)
_ENV_ROTATOR_MODEL = os.getenv("COELHO_LLM_MODEL", "auto").strip() or "auto"
_ENV_API_KEY = os.getenv("COELHO_LLM_API_KEY", "").strip()

_ENDPOINT_RESOLVE_TTL_S = 10.0
_endpoint_resolved_at = 0.0

COELHO_ROTATOR_URL = _normalize_base_url(_ENV_ROTATOR_URL)
COELHO_ROTATOR_MODEL = _ENV_ROTATOR_MODEL
COELHO_API_KEY = _ENV_API_KEY or "dummy"


def _resolve_endpoint(*, force_store: bool = False) -> tuple[str, str, str]:
    """(base_url, api_key, model) with Settings-page override applied."""
    url, model, key = _ENV_ROTATOR_URL, _ENV_ROTATOR_MODEL, _ENV_API_KEY
    try:
        from domains.llm.credentials import get_store, resolve_key

        ep = (get_store().read_settings(force=force_store) or {}).get("llm_endpoint")
        if isinstance(ep, dict):
            url = (ep.get("url") or "").strip() or url
            model = (ep.get("model") or "").strip() or model
        k = (resolve_key("COELHO_LLM_API_KEY") or "").strip()
        if k:
            key = k
    except Exception as e:  # store/import miss → env + default
        logger.debug(f"[rotator-adapter] endpoint resolve store miss: {e}")
    return _normalize_base_url(url), (key or "dummy"), (model or "auto")


def _apply_endpoint(*, force: bool = False) -> bool:
    """Re-resolve + reassign the module globals. Returns True if any changed."""
    global COELHO_ROTATOR_URL, COELHO_API_KEY, COELHO_ROTATOR_MODEL
    global _endpoint_resolved_at
    now = time.monotonic()
    if not force and (now - _endpoint_resolved_at) < _ENDPOINT_RESOLVE_TTL_S:
        return False
    _endpoint_resolved_at = now
    url, key, model = _resolve_endpoint(force_store=force)
    if (url, key, model) == (COELHO_ROTATOR_URL, COELHO_API_KEY, COELHO_ROTATOR_MODEL):
        return False
    COELHO_ROTATOR_URL, COELHO_API_KEY, COELHO_ROTATOR_MODEL = url, key, model
    logger.info(
        f"[rotator-adapter] endpoint updated → {url} model={model} "
        f"key={'set' if key != 'dummy' else 'none'}"
    )
    return True


_apply_endpoint(force=True)


def is_bundled_rotator() -> bool:
    """Always False (2026-09-11) — Nexus no longer bundles or deploys its own
    rotator; every endpoint, including the default, is a separately-managed
    service. Kept as a stable shim so the few remaining callers don't need to
    branch on removed state."""
    return False


def is_external_endpoint() -> bool:
    """Always True (2026-09-11) — whatever endpoint is configured owns its own
    provider keys, so Nexus's NIM-key readiness gate never applies. Kept as a
    named predicate (rather than inlining `True`) so discovery/service.py's
    readiness gate still reads as intentional."""
    return True

# 2026-09-12: dropped the client-side `_hidden_params.custom_llm_provider`
# prefix-guessing that used to live here — dead on arrival in this thin HTTP
# adapter (`resp` is an OpenAI-SDK `ChatCompletion` from an HTTP response
# body, which never carries litellm's in-process-only `_hidden_params`) and
# superseded anyway: the rotator itself now returns an already-prefixed
# "PROVIDER/model" string in `resp.model`
# (COELHOLLMRotator chain/domain.py::display_model_id).

# ---------------------------------------------------------------------------
# Pooled AsyncOpenAI singleton — SOTA httpx limits + http2
# ---------------------------------------------------------------------------

import httpx as _httpx

_CLIENT: object | None = None
_CLIENT_LOCK = asyncio.Lock()

def _build_limits() -> _httpx.Limits:
    # SOTA Sept 2026: max_connections 200 / max_keepalive 100 / expiry 30s
    # Lets 24 concurrent doc_distill + 20 off_topic share pool without exhaustion.
    # Baseten & Decodo benchmarks show break-even vs aiohttp at ~200 concurrent with these.
    return _httpx.Limits(
        max_connections=200,
        max_keepalive_connections=100,
        keepalive_expiry=30.0,
    )

def _build_timeout(timeout_s: float | None) -> _httpx.Timeout:
    # Connect short, read covers LLM TTFT + generation; write short.
    # httpx.Timeout defaults 5s is too tight for cold starts. This is the
    # client-level ceiling used once at construction (_get_async_openai
    # passes None here) — every real call passes its own `timeout` kwarg
    # which the SDK uses per-request instead, but the ceiling itself was
    # previously 30s, smaller than several real per-call timeouts already
    # in use (45-90s) — an inconsistent floor to fall back to if a
    # per-request override ever failed to apply. Raised to the largest
    # per-call timeout actually used across planner/synth nodes.
    t = timeout_s or 90.0
    return _httpx.Timeout(timeout=t, connect=5.0, read=t, write=5.0, pool=5.0)

async def _get_async_openai():
    """Singleton AsyncOpenAI with pooled httpx client (lazy, thread-safe for async)."""
    global _CLIENT
    # Pick up a Settings-page endpoint change (throttled store re-read). A
    # process that didn't call reset_rotator() itself (e.g. the celery worker
    # when the change was made from fastapi) converges within _ENDPOINT_RESOLVE_TTL_S.
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

        # Use a shared AsyncClient with pooling; http2 multiplexing if h2 is installed
        # httpx[http2] extra is not in base image, so gracefully fall back to http/1.1 keep-alive
        try:
            http_client = _httpx.AsyncClient(
                limits=_build_limits(),
                http2=True,
                timeout=_build_timeout(None),
                follow_redirects=True,
            )
            http2_enabled = True
        except ImportError:
            http_client = _httpx.AsyncClient(
                limits=_build_limits(),
                http2=False,
                timeout=_build_timeout(None),
                follow_redirects=True,
            )
            http2_enabled = False
        client = _openai.AsyncOpenAI(
            base_url=COELHO_ROTATOR_URL,
            api_key=COELHO_API_KEY,
            max_retries=0,  # rotator handles cascade, SDK retries would triple timeout
            http_client=http_client,
        )
        _CLIENT = client
        logger.info(f"[rotator-adapter] AsyncOpenAI pooled client → {COELHO_ROTATOR_URL} model={COELHO_ROTATOR_MODEL} (http2={http2_enabled}, 200/100 pool)")
        return client

def _get_openai_sync():
    """Sync client fallback for embed/rerank paths that may be called sync."""
    import openai as _openai
    try:
        http_client = _httpx.Client(limits=_build_limits(), http2=True, timeout=_build_timeout(None))
    except ImportError:
        http_client = _httpx.Client(limits=_build_limits(), http2=False, timeout=_build_timeout(None))
    return _openai.OpenAI(
        base_url=COELHO_ROTATOR_URL,
        api_key=COELHO_API_KEY,
        max_retries=0,
        http_client=http_client,
    )

# ---------------------------------------------------------------------------
# Helpers — ChatOpenAI / OpenAIEmbeddings via endpoint (legacy compat)
# ---------------------------------------------------------------------------

_SHARED_HTTP_CLIENT: object | None = None


def _get_shared_http_client():
    """Module-level pooled `httpx.AsyncClient` shared by every `ChatOpenAI`
    built below (lazily created, no lock — worst case two racing callers
    each build one and one wins; both are functionally identical pools).

    Same pool shape as the raw hot path (`_build_limits` + http2 with
    http/1.1-keepalive fallback). If the Settings-page endpoint moves,
    the pool is transport-agnostic (per-request base_url comes from the
    SDK client, not this transport), so nothing needs rebuilding here —
    only `_get_async_openai`'s bound client needs the reset treatment."""
    global _SHARED_HTTP_CLIENT
    if _SHARED_HTTP_CLIENT is not None:
        return _SHARED_HTTP_CLIENT
    try:
        client = _httpx.AsyncClient(
            limits=_build_limits(),
            http2=True,
            timeout=_build_timeout(None),
            follow_redirects=True,
        )
    except ImportError:
        client = _httpx.AsyncClient(
            limits=_build_limits(),
            http2=False,
            timeout=_build_timeout(None),
            follow_redirects=True,
        )
    _SHARED_HTTP_CLIENT = client
    return client


def _build_chat_openai(
    *,
    timeout_s:       float | None = None,
    max_tokens:      int | None   = None,
    temperature:     float | None = None,
    response_format: dict | None  = None,
    rotator_task:    str | None   = None,
    max_wall_s:      float | None = None,
    max_retries:     int          = 0,
):
    """Shared LangChain `ChatOpenAI` construction for every non-hot-path
    caller — one place to keep `max_retries=0` applied consistently BY
    DEFAULT. Rotator handles cascade server-side; an SDK-level retry
    loop on top of it would stack a second, redundant retry for any
    caller that already retries itself (same reasoning as the raw-
    client hot path's `max_retries=0` in `_get_async_openai`).

    2026-09-18: `max_retries` made overridable (still defaults to 0 —
    every existing caller is unaffected) for callers with NO retry
    protection of their own. Root-caused live: RR's orchestrator/
    subagent models (`build_rr_strong_chain`, passed directly to
    DeepAgents as the bare model object) can't use `resilient_ainvoke`
    like YCS Ask or RR's own backfill/code_synth do — wrapping the
    model breaks DeepAgents' `isinstance(model, BaseChatModel)` check.
    With zero retry anywhere, one transient rotator 504 (confirmed
    live: hit the rotator's own 600s max_wall_s ceiling, 90% into a
    scan, 7/8 extractions already done) crashed the ENTIRE scan,
    discarding all prior progress. `max_retries` at the SDK level is
    the only retry mechanism that doesn't touch the model's class, so
    it's the one lever compatible with DeepAgents' bare-model
    requirement.

    2026-09-13: added after finding TWO independent `ChatOpenAI`
    construction sites (`_get_chat_llm` and YCS's Neo4j chain builder)
    had each separately forgotten this — consolidating so it can't
    drift apart a third time.

    2026-09-14: `rotator_task` (optional — most callers stay untagged,
    which the rotator defaults server-side to `"general"`) sets
    `extra_body={"metadata": {"rotator_task": ...}}`, verified against
    the rotator's actual source
    (`~/Workbench/COELHOLLMRotator/apps/fastapi/domains/llm/rotator/
    bandit/service.py`'s `cell_key(deployment, task)`) to genuinely
    partition the FGTS-VA bandit's per-arm statistics by task, not
    just tag requests for observability — DD's raw-`AsyncOpenAI` hot
    path already does the equivalent via `chat_judge_bandit_async`'s
    own `extra_body`; this brings the same calibration to
    `ChatOpenAI`-based callers, starting with YCS's Neo4j chain
    (`build_ycs_neo4j_pinned_chain` below), which previously sent every
    call untagged despite being a distinctly heavy shape (full-
    transcript input, large JSON completion) that shouldn't be judged
    by the same pooled stats as lighter untagged traffic.

    2026-09-14: `max_wall_s` (optional) sets
    `extra_body.metadata.max_wall_s` — a per-request override of the
    rotator's own internal wall-clock safety valve
    (`_MAX_CASCADE_WALL_S`, default 180s, in the rotator's
    `api/v1/llm/openai/router.py`). Root-caused: the rotator is a
    UNIVERSAL multi-project server, and 180s was calibrated for DD's
    Synth specifically — it was silently capping every request's total
    time (all internal retries included) regardless of what `timeout_s`
    this client set, which is why raising `timeout_s` alone never
    helped YCS's Neo4j extraction (full-transcript input, large JSON
    completion, routinely needs 300-400s). This asks the rotator for
    more budget WITHOUT touching its shared default for every other
    caller/project.

    2026-09-15: every instance now shares ONE module-level pooled
    `httpx.AsyncClient` (`_get_shared_http_client`, same 200/100 pool +
    http2-fallback shape as the raw hot path's client) instead of each
    `ChatOpenAI(...)` allocating its own pool. Planner-proven: reuses
    TCP/keep-alive across calls, avoids ~15ms + TLS/handshake per-call
    alloc. Per-request `timeout_s` still governs each call (the SDK
    prefers it over the transport default)."""
    from langchain_openai import ChatOpenAI

    kwargs: dict = {
        "base_url":          COELHO_ROTATOR_URL,
        "api_key":           COELHO_API_KEY,
        "model":             COELHO_ROTATOR_MODEL,
        "temperature":       temperature if temperature is not None else 0.0,
        "max_retries":       max_retries,
        "http_async_client": _get_shared_http_client(),
    }
    if timeout_s is not None:
        kwargs["timeout"] = timeout_s
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    if response_format is not None:
        kwargs["model_kwargs"] = {"response_format": response_format}
    if rotator_task or max_wall_s is not None:
        metadata: dict = {}
        if rotator_task:
            metadata["rotator_task"] = rotator_task
        if max_wall_s is not None:
            metadata["max_wall_s"] = max_wall_s
        kwargs["extra_body"] = {"metadata": metadata}
    return ChatOpenAI(**kwargs)


def _get_chat_llm(
    *,
    max_tokens: int | None = None,
    temperature: float | None = None,
    timeout_s: float | None = None,
    response_format: dict | None = None,
):
    """Legacy LangChain wrapper — retained for non-hot paths.

    Hot path (doc_distill / off_topic / chapter_assign) now uses raw AsyncOpenAI
    via _get_async_openai() for lower overhead. This stays for backward-compat
    callers that pass LangChain messages.
    """
    return _build_chat_openai(
        timeout_s       = timeout_s,
        max_tokens      = max_tokens,
        temperature     = temperature,
        response_format = response_format,
    )


# ------------------------------------------------------------------
# Chat — judge + bandit (bandit args ignored, single auto pool)
# SOTA: raw AsyncOpenAI pooled, no per-call ChatOpenAI construction
# ------------------------------------------------------------------

async def chat_judge_async(
    prompt: str,
    max_tokens: int = 8,
    temperature: float = 0.0,
) -> str:
    # Hot path delegates to pooled bandit version with minimal overhead
    text, _ = await chat_judge_bandit_async(prompt, max_tokens=max_tokens, temperature=temperature)
    return text


async def chat_judge_bandit_async(
    prompt: str,
    *,
    max_tokens: int = 8,
    temperature: float = 0.0,
    timeout_s: float = 30.0,
    expected_pattern: str | None = None,
    dd_process: str | None = None,  # ignored — no per-process weights
    candidate_filter=None,  # ignored — no heavyweight filter
    response_format: dict | None = None,
) -> tuple[str, dict]:
    # SOTA: direct OpenAI SDK → rotator, pooled keep-alive, no LangChain translation
    client = await _get_async_openai()

    # Build OpenAI-compatible kwargs — only send non-None to stay minimal
    kwargs: dict = {
        "model": COELHO_ROTATOR_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "timeout": timeout_s,
        # Wave H1: opaque per-call-site task label → the rotator uses it ONLY as
        # a bandit-cell namespace, never interprets it. Isolates DD workloads'
        # learning from each other and from generic callers.
        "extra_body": {"metadata": {"rotator_task": f"dd-{dd_process}" if dd_process else "dd"}},
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
    # internal SDK retry loop that could eat this budget out from under it
    # (a naive outer wait_for without that guarantee is not sufficient —
    # see openai/instructor-style retry loops silently outliving an outer
    # wait_for in other projects).
    backstop_s = (timeout_s or 30.0) + 15.0
    _rotator_request_id: str | None = None

    async def _do_call():
        nonlocal _rotator_request_id
        try:
            raw = await client.chat.completions.with_raw_response.create(**kwargs)  # type: ignore[arg-type]
            try:
                _rotator_request_id = raw.headers.get("x-rotator-request-id")
            except Exception:
                pass
            return raw.parse()
        except (AttributeError, TypeError):
            # older SDK without with_raw_response — lose the request id, keep working
            return await client.chat.completions.create(**kwargs)  # type: ignore[arg-type]

    try:
        resp = await asyncio.wait_for(_do_call(), timeout = backstop_s)
    except asyncio.TimeoutError as e:
        raise TimeoutError(
            f"chat_judge_bandit_async hard backstop fired after "
            f"{backstop_s:.0f}s (requested timeout_s={timeout_s})"
        ) from e
    except Exception as e:
        # Normalize error for upstream classify_error (preserve message)
        raise e

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

    # Deployment surfacing — resp.model is the real arm, not the group alias.
    # 2026-09-12: dropped the client-side `_hidden_params.custom_llm_provider`
    # prefix-guessing that used to live here — dead on arrival in this thin
    # HTTP adapter (`resp` is an OpenAI-SDK `ChatCompletion` from an HTTP
    # response body, which never carries litellm's in-process-only
    # `_hidden_params`) and superseded anyway: the rotator itself now returns
    # an already-prefixed "PROVIDER/model" string in `resp.model`
    # (COELHOLLMRotator chain/domain.py::display_model_id).
    deployment = COELHO_ROTATOR_MODEL
    try:
        m = getattr(resp, "model", None)
        if isinstance(m, str) and m:
            deployment = m
        elif isinstance(resp, dict) and resp.get("model"):
            deployment = str(resp["model"])
        # Coerce bare :free → openrouter prefix (fallback for the rare case
        # the rotator's own prefixing didn't apply — e.g. a provider not yet
        # in its display-name table).
        low = deployment.lower()
        if (low.endswith(":free") or "minimax-m3" in low or "dots-" in low) and "openrouter" not in low and "/" not in deployment:
            deployment = f"openrouter/{deployment}"
    except Exception:
        pass

    meta = {
        "deployment": deployment,
        "attempts": 1,
        "latency_s": round(latency_s, 3),
        "reward": None,
        "dd_process": dd_process or "auto",
        "rotator_request_id": _rotator_request_id,
    }

    if expected_pattern:
        try:
            if not re.compile(expected_pattern).match(text.split()[0].strip(".,;:!\"'`") if text else ""):
                meta["schema_invalid"] = True
        except Exception:
            pass

    # Usage extraction for counter (prompt/completion tokens)
    try:
        usage = getattr(resp, "usage", None)
        if usage is not None:
            # Build AIMessage-like shim for bump
            class _Msg:
                content = text
                response_metadata = {"model_name": deployment}
                usage_metadata = {
                    "input_tokens": getattr(usage, "prompt_tokens", 0) or 0,
                    "output_tokens": getattr(usage, "completion_tokens", 0) or 0,
                }
            _bump_dd_llm_counter(_Msg(), deployment=deployment)
        else:
            # Fallback generic bump without usage
            class _Msg2:
                content = text
                response_metadata = {"model_name": deployment}
                usage_metadata = {}
            _bump_dd_llm_counter(_Msg2(), deployment=deployment)
    except Exception:
        pass

    return text, meta


def _bump_dd_llm_counter(response, deployment: str | None = None) -> dict | None:
    try:
        import domains

        # Adapt to bump_current_call expected shape
        fake_resp = {
            "model": deployment or COELHO_ROTATOR_MODEL,
            "usage": getattr(response, "usage_metadata", None) or {},
            "choices": [{"message": {"content": getattr(response, "content", "")}}],
        }
        return domains.dd.runtime.service.bump_current_call(
            response=fake_resp, deployment=deployment or COELHO_ROTATOR_MODEL,
        )
    except Exception as e:
        logger.debug(f"[rotator-adapter] bump failed: {e}")
        return None


# PEP 562 — any missing build_* import returns the generic ChatOpenAI adapter
# (covers old chain-builder names — build_curator_llm, build_keylm_chain,
# build_pinned_chain_any, build_refine_llm_chain, build_resolver_llm_chain,
# build_synth_*, build_ycs_neo4j_pinned_chain — without keeping a dead
# explicit stub per name).
def __getattr__(name: str):
    if name.startswith("build_"):
        def _stub(*args, **kwargs):
            return build_reduce_label_chain(*args, **kwargs)

        return _stub
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def reset_rotator(*args, **kwargs) -> None:
    # Re-resolve the endpoint (Settings page just changed it) then drop the
    # pooled client so the next call rebuilds against the new URL/key/model.
    global _CLIENT
    try:
        _apply_endpoint(force=True)
    except Exception:
        pass
    try:
        if _CLIENT is not None:
            # Close underlying http_client gracefully
            import asyncio as _asyncio
            try:
                # _CLIENT is AsyncOpenAI with .close()
                if hasattr(_CLIENT, "close"):
                    # Don't await in sync context; schedule if loop running
                    try:
                        loop = _asyncio.get_running_loop()
                        loop.create_task(_CLIENT.close())  # type: ignore
                    except RuntimeError:
                        pass
            except Exception:
                pass
        _CLIENT = None
    except Exception:
        pass
    return None


def build_reduce_label_chain(
    *,
    timeout_s:    float | None = 600.0,
    rotator_task: str | None   = None,
):
    """Generic external-provider `ChatOpenAI` — base_url/model come from
    the Settings-page override (or env/default), NOT from any in-Nexus
    pool. 2026-09-15: routes through `_build_chat_openai` so
    `max_retries=0` is applied (the rotator cascades server-side; an
    SDK-level retry loop on top would stack a redundant retry, hiding
    clean failures from the caller's own handling). Default 600s
    ceiling matches the SDK implicit default — callers with tighter
    budgets (NL-to-DSL translation, health checks) pass their own
    `timeout_s` + `rotator_task` tag instead."""
    return _build_chat_openai(
        timeout_s    = timeout_s,
        rotator_task = rotator_task,
    )


def build_llm_fallback_chain(
    *,
    timeout_s:    float | None = 600.0,
    rotator_task: str | None   = None,
):
    return build_reduce_label_chain(
        timeout_s    = timeout_s,
        rotator_task = rotator_task,
    )


# ------------------------------------------------------------------
# Legacy shims — keep imports from chain/__init__.py working. Every
# build_* name not explicitly defined above falls through to the
# module __getattr__ below (returns build_reduce_label_chain(...)), so
# only names with real external callers whose behavior would otherwise
# change (async vs sync, a real return value vs None) are kept explicit
# here. 2026-09-12: dropped build_curator_llm / build_keylm_chain /
# build_pinned_chain_any / build_refine_llm_chain / build_resolver_llm_chain
# / build_synth_fallback_chain / build_synth_pinned_chain /
# build_synth_pool_chain (zero-diff via the generic build_* fallback),
# get_entries_for_group / get_parent_group / pick_synth_deployment /
# pick_synth_deployment_bandit (zero real callers anywhere), and
# _get_router / _redis_for_bandit (zero real callers).
#
# ensure_dynamic_catalog stays explicit — real callers `await` it, and the
# __getattr__ fallback below returns a plain sync lambda, which would raise
# `TypeError: object NoneType can't be used in 'await' expression'.
#
# 2026-09-13: pick_ycs_neo4j_deployment_bandit / record_ycs_neo4j_reward /
# release_ycs_provider_slot were REMOVED (not just fixed) once confirmed
# to be pure no-ops that neo4j_task/task.py's "arm-swap" loop was calling
# for no effect — the bandit-pick always returned the same generic
# target regardless of any exclusion set, and the reward/slot functions
# did nothing at all. There is no local arm-pool to pin/reward/release
# anymore; the rotator's own FGTS-VA bandit picks per HTTP call when
# model="auto" (same as every other build_* consumer). neo4j_task now
# calls build_ycs_neo4j_pinned_chain() directly and retries failed
# videos in-place instead of "swapping arms."
# ------------------------------------------------------------------
async def ensure_dynamic_catalog(*args, **kwargs):
    return None


def build_ycs_neo4j_pinned_chain(pinned_model: str | None = None, *args, **kwargs):
    # pinned_model is accepted for call-site compat but ignored — the
    # rotator picks per HTTP call (model="auto"), same as every other
    # build_* consumer. Explicit (not the generic __getattr__ stub)
    # because that stub forwards to the zero-arg build_reduce_label_chain,
    # which raises TypeError on the positional pinned_model argument
    # task.py actually passes.
    #
    # 2026-09-13: no longer delegates to build_reduce_label_chain() — that
    # bare ChatOpenAI(...) had no timeout override and no max_retries=0,
    # so it fell back to the openai SDK's own defaults (600s timeout,
    # max_retries=2). An SDK-level retry there stacks a second, redundant
    # retry loop on top of the rotator's own arm-swap cascade — the
    # rotator returns a 504 in ~20-30s when it's genuinely giving up, and
    # the SDK would silently retry that itself before neo4j_task's own
    # circuit breaker ever saw a clean failure to react to. max_retries=0
    # fixes that regardless of the timeout value.
    #
    # 2026-09-13 CORRECTION (same day): first shipped with timeout_s=120.0,
    # reasoning "comfortably above the observed 20-30s 504 latency." Wrong
    # — that 20-30s figure was the rotator's fast-fail path; a genuinely
    # slow-but-working call for this workload (large transcripts → large
    # completions) legitimately needs more. Live-tested at both
    # concurrency=5 AND concurrency=3: every first-batch call timed out
    # simultaneously at exactly 120s with ZERO successes, ruling out
    # concurrency/contention as the cause — 120s was just too tight for
    # this call shape. Raised to 400s: still comfortably under the 600s
    # GRAPH_BATCH_TIMEOUT_S outer watchdog in graph_builder/service.py,
    # but much closer to the ~600s implicit default that was actually
    # working (with occasional real 504s) before any of today's changes.
    #
    # 2026-09-14: `rotator_task="ycs-neo4j-extract"` — see
    # `_build_chat_openai`'s docstring. Every call from this chain was
    # previously untagged (defaults server-side to `"general"`), so the
    # rotator's bandit had never built calibrated statistics for THIS
    # workload's shape (full-transcript input, large JSON completion) —
    # it was judging models against whatever mix of lighter untagged
    # traffic happens to also land in "general". Tagging gives it a
    # dedicated cell to actually learn which deployments handle large-
    # context extraction well, the same mechanism DD's `dd-{dd_process}`
    # tags already exploit per node type.
    #
    # 2026-09-14: `max_wall_s` — root-caused the persistent ~180s 504s
    # to the ROTATOR's OWN internal wall-clock safety valve
    # (`_MAX_CASCADE_WALL_S`, default 180s in the rotator's
    # `api/v1/llm/openai/router.py`), which caps total time across ALL
    # its internal retries/cascade attempts — completely independent of
    # this client's own `timeout_s`. First shipped at 350.0 (under the
    # client's then-400.0s timeout); a real isolated call still used
    # the full budget and failed, and the user's priority is
    # completeness (don't discard relationships) over speed — bigger
    # completions need more room, not less. Raised to 600.0 — the
    # rotator's own `_MAX_WALL_S_CEILING`, so this is the most this
    # specific call can ever ask for; `timeout_s` raised to 650.0 to
    # stay above it (this client must never cut the rotator's cascade
    # off mid-flight — see the 350/400 pairing's rationale above).
    # `GRAPH_BATCH_TIMEOUT_S` (graph_builder/params.py, 700s) sits
    # above BOTH so ITS OWN watchdog doesn't fire first either.
    return _build_chat_openai(
        timeout_s    = 650.0,
        rotator_task = "ycs-neo4j-extract",
        max_wall_s   = 600.0,
    )


def build_rr_strong_chain(
    *,
    rotator_task: str | None   = None,
    temperature:  float | None = None,
    max_retries:  int          = 0,
) -> BaseChatModel:
    """Research Radar's strong-tier `ChatOpenAI` — orchestrator, subagents,
    and the two one-off callers outside the DeepAgents loop (task.py's
    backfill, code_synth's 3-round generate/critique/revise) all build
    through here now.

    2026-09-17: replaces the old `build_rr_strong_chain`/
    `build_rr_strong_chain_bandit` pair, which no longer existed as real
    functions — both names silently fell through `chain/service.py`'s
    generic `__getattr__` stub to a bare, UNTAGGED
    `build_reduce_label_chain()` (bucketed under the rotator's "general"
    cell). `graph.py`'s surrounding comments still described a client-
    side "10-arm pool (7 NIM frontier + 2 Mistral direct + 1 SambaNova
    free-tier 405B)" and a `KD_RR_BANDIT_CHAT` flag choosing between a
    "LiteLLM Router simple-shuffle chain" and a "bandit-routed chain" —
    neither exists anymore; every arm/deployment pick happens server-
    side in the external COELHO LLM Rotator (model="auto", the same
    Settings-page-configured endpoint every other build_* here talks
    to), same as DD's Planner/Synth and YCS's Ingestion/Ask/Query. The
    two names always resolved to the exact same call, so the flag was
    already dead — removed along with it.

    `rotator_task` (e.g. "rr-orchestrator", "rr-subagent", "rr-backfill",
    "rr-code-synth") gives the bandit a dedicated cell per call shape,
    same reasoning as `build_ycs_neo4j_pinned_chain` above — RR's calls
    span short JSON extractions and long code-gen completions; pooling
    them all under "general" (or even one shared "rr" tag) would blur
    the bandit's per-shape statistics. `timeout_s`/`max_wall_s` reuse
    the same 650s/600s budget as the Neo4j chain — RR's deep_read/
    synthesis/code_synth calls carry comparably large context+completion
    shapes (full paper text in, 150-400 line files or multi-field JSON
    out).

    2026-09-18: `max_retries` (default 0, unchanged) — RR's task.py
    backfill and code_synth callers already wrap their calls in
    `resilient_ainvoke` and should leave this at 0 (an SDK retry on
    top would double-stack). `graph.py`'s orchestrator/subagent
    factories pass `max_retries=1` — those calls run inside DeepAgents'
    own loop with no caller-side retry at all (wrapping the model
    breaks DeepAgents' `isinstance(model, BaseChatModel)` check), so
    this is their only protection against a transient rotator 5xx —
    see `_build_chat_openai`'s docstring for the live incident that
    motivated this."""
    return _build_chat_openai(
        timeout_s    = 650.0,
        rotator_task = rotator_task,
        max_wall_s   = 600.0,
        temperature  = temperature,
        max_retries  = max_retries,
    )


# ── Wave H2 — quality feedback to the rotator ────────────────────────────────
# DD nodes can judge their own output (valid distillate vs fallback, verdict
# shape, key-term grounding). Fire-and-forget a 0-1 score to the rotator's
# /routing/feedback keyed by meta["rotator_request_id"] — closes the FGTS-VA
# learning loop with a DD-computed signal the rotator never has to understand.
# Gated by KD_ROTATOR_FEEDBACK=1 (default off).

def _feedback_enabled() -> bool:
    return os.environ.get("KD_ROTATOR_FEEDBACK", "").strip().lower() in ("1", "true", "yes", "on")


async def submit_feedback(rotator_request_id: str | None, quality: float) -> None:
    """POST {request_id, quality} to <rotator>/routing/feedback. Best-effort,
    never raises, ~2s cap. Call via asyncio.create_task — do not await inline."""
    if not rotator_request_id or not _feedback_enabled():
        return
    try:
        base = COELHO_ROTATOR_URL.rsplit("/openai/v1", 1)[0]
        url = f"{base}/routing/feedback"
        async with _httpx.AsyncClient(timeout=2.0) as c:
            await c.post(url, json={
                "request_id": rotator_request_id,
                "quality": max(0.0, min(1.0, float(quality))),
            })
    except Exception as e:
        logger.debug(f"[rotator-adapter] submit_feedback: {e}")


def fire_feedback(meta: dict | None, quality: float) -> None:
    """Sync helper: schedule submit_feedback from a node without awaiting."""
    if not meta:
        return
    try:
        asyncio.create_task(submit_feedback(meta.get("rotator_request_id"), quality))
    except RuntimeError:
        pass  # no running loop — skip
