"""Unified LLM Router — LiteLLM-backed with fail-fast pre-call checks.

ONE ranked catalog (`dd-all`) reused by every DD step; temperature differs per
step (T=0.7 for Self-Refine, T=0.0 elsewhere), priority is identical. All
providers are free-tier — quality is the only optimization objective.

LiteLLM Router features:
  - `enable_pre_call_checks=True` — cooled-down deployments are filtered
    BEFORE the call fires (0ms skip)
  - `allowed_fails_policy` per error type
  - `cooldown_time=60s` Redis-backed (shared across Celery workers)
  - `routing_strategy="simple-shuffle"` (LiteLLM-recommended for production)

SECURITY: litellm pinned to 1.83.12 (v1.82.7/1.82.8 compromised 2026-03-24).
Do NOT allow litellm>=1.82.7,<1.83.0.

Dynamic catalog (DD_DYNAMIC_CATALOG=1 default): discovery + benchmarks → top-K
per step. Falls back to the static catalog on any failure. See
init_dynamic_catalog. BYOK selection (llm/settings.json) filters EVERYWHERE
via the `*_current()` accessors.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import httpx
import litellm
import redis.asyncio as redis_aio
from litellm import Router
from litellm.types.router import AllowedFailsPolicy, RetryPolicy
from langchain_litellm.chat_models import ChatLiteLLMRouter


# --------------------------------------------------------------------------- #
# Cross-provider request sanitization — module-level LiteLLM toggles.
#
# 2026-06-12: enables LiteLLM's automatic per-request message + param
# sanitization so the Router can freely cascade across mixed-capability
# providers without 400s.
#
# Why we need this:
#   The dd-all pool mixes reasoning models (qwen3.5-397b-a17b w/ thinking,
#   deepseek-v4-flash) with non-reasoning chat-only providers (Groq Llama,
#   Mistral, Gemini Flash). When a reasoning arm emits an AIMessage with
#   content=[{type:thinking, ...}] and the Router's simple-shuffle then
#   routes the NEXT turn to a non-reasoning arm, the new provider's API
#   declares `content: str` and rejects the list with a Pydantic 400. The
#   cascade exhausts → ChatLiteLLMRouter returns empty generations →
#   langchain_core/chat_models.py:508 IndexError: list index out of range
#   (observed end-to-end on the RR agent's first scan smoke test).
#
# `modify_params` walks the message list before every request and DROPS
# thinking_blocks / reasoning_content from assistant messages when the
# target provider's schema can't accept them (and preserves them when it
# can, e.g. Anthropic, DeepSeek V4 Pro). Also handles orphaned tool_calls.
#
# `drop_params` strips per-call parameters the target provider doesn't
# support — eliminates the `nvidia/nemotron-4-340b-instruct does not
# support parameters: ['tools']` UnsupportedParamsError class.
#
# Both flags are idempotent + module-level so they take effect for every
# litellm.acompletion call in this process (router-mediated AND direct).
# Pinned LiteLLM 1.83.13 has both since the 1.7x line.
# See docs.litellm.ai/docs/completion/message_sanitization +
# docs.litellm.ai/docs/completion/drop_params.
# --------------------------------------------------------------------------- #
litellm.modify_params = True
litellm.drop_params   = True


from domains.llm.credentials import resolve_key
from domains.llm.rotator import bandit, benchmarks, discovery

from .config import DYNAMIC_STEPS, JUDGE
from .domain import (
    classify_error,
    entry_provider_and_model,
    is_eol_error,
    is_non_chat_model,
    passes_capability_floor,
    provider_key_env,
    provider_mode,
    selection_allows,
)
from .keys import (
    DD_EMBED_GROUP,
    DD_EMBED_MODEL_NAME,
    DD_RERANK_MODEL_NAME,
    GROUP,
    KEYLM_GROUP,
    REDUCE_LABEL_GROUP,
    RR_STRONG_GROUP,
    SYNTH_GROUP,
    _JUDGE_KD_PROCESS,
    _NIM_RERANK_BASE,
    _RESPONSE_FORMAT_SAFE_PROVIDERS,
    _SETTINGS_GEN_REDIS_KEY,
)
from .params import (
    DD_EMBED_BATCH_SIZE,
    _ARM_COOLDOWN_S,
    _DYNAMIC_MIN_PARAM_B,
    _DYNAMIC_QUALITY_FLOOR_STEPS,
    _GEN_THROTTLE_S,
    _NIM_REQUIRED_MSG,
    _PROVIDER_CHAPTER_CAPS,
)
from ..observability import (
    genai_bandit_attempt_span,
    genai_bandit_cascade_span,
    genai_completion_span,
    genai_embedding_span,
    genai_embedding_span_sync,
    genai_rerank_span,
    update_bandit_outcome,
)
from ..observability.domain import system_for_deployment


logger = logging.getLogger(__name__)


# Kill the OpenAI SDK's hidden retries at the source (2026-06-10).
# Retry policy in this codebase is owned by the LiteLLM Router layer
# (RetryPolicy per error class + bandit arm selection across runs) —
# but litellm 1.83.13 constructs every OpenAI-compatible client with
# `data.pop("max_retries", 2)`, and the per-call/`litellm_params`
# `max_retries` is STRIPPED by the param mapper for openai-compatible
# providers (verified in-container: per-call 0 AND 1 both arrived as
# max_retries=2). Net effect: every router attempt was silently 3 SDK
# attempts, each burning the full per-deployment timeout (observed:
# step-3.5-flash 36-min batches = 4 router x 3 SDK x 180s). There is
# no litellm-level knob that reaches this path, so we pin it at the
# SDK boundary: every (Async)OpenAI client in this process is built
# with max_retries=0. Safe scope: no other code here constructs
# OpenAI SDK clients (YCS embeddings use raw httpx), and connection-
# class errors still retry once via the Router's num_retries fallback.
try:
    import openai as _openai_sdk

    if not getattr(_openai_sdk, "_kd_no_sdk_retries", False):
        _orig_async_openai_init = _openai_sdk.AsyncOpenAI.__init__
        _orig_sync_openai_init = _openai_sdk.OpenAI.__init__

        def _async_openai_init_no_retries(self, *args, **kwargs):
            kwargs["max_retries"] = 0
            return _orig_async_openai_init(self, *args, **kwargs)

        def _sync_openai_init_no_retries(self, *args, **kwargs):
            kwargs["max_retries"] = 0
            return _orig_sync_openai_init(self, *args, **kwargs)

        _openai_sdk.AsyncOpenAI.__init__ = _async_openai_init_no_retries
        _openai_sdk.OpenAI.__init__ = _sync_openai_init_no_retries
        _openai_sdk._kd_no_sdk_retries = True
        logger.info("[llm-chain] OpenAI SDK hidden retries disabled (max_retries=0)")
except Exception as _sdk_patch_err:
    logger.warning(
        f"[llm-chain] failed to disable OpenAI SDK retries "
        f"({type(_sdk_patch_err).__name__}: {_sdk_patch_err})"
    )


# --------------------------------------------------------------------------- #
# Mutable module state (Router + caches + dynamic catalog)
# --------------------------------------------------------------------------- #
_router_instance: Router | None = None
_pinned_chain_cache: dict[str, ChatLiteLLMRouter] = {}
_pinned_to_parent: dict[str, str] = {}

_dynamic_entries: dict[str, list[dict]] = {}
_dynamic_catalog_initialized: bool = False
_dynamic_built_gen: int = -1

_settings_gen_local: int = 0
_settings_gen_cache: int = 0
_settings_gen_read_at: float = 0.0

_arm_cooldown: dict[str, float] = {}


# --------------------------------------------------------------------------- #
# RR parallel-LLM cap (Wave 1.5 — 2026-06-16)
# --------------------------------------------------------------------------- #
# Caps in-flight bandit-routed LLM calls per asyncio event loop. Mirrors the
# Planner/Synth pattern of `asyncio.Semaphore(_CONCURRENCY)` per node — but
# applied at the rotator-chain layer because RR's subagents fan out via
# DeepAgents (no node-level handle to gate). Per-loop because each Celery
# task creates its own asyncio.run loop; one Semaphore per scan is what
# we want.
#
# Default 8 = same cap Planner uses on doc_distill / chapter_assign. Env
# `KD_RR_SEM` overrides. The cap protects against:
#   - free-tier provider RPM bursts (NIM 40 RPM, Groq 30 RPM) when the
#     orchestrator + N subagents + bandit cascades collide
#   - bandit reward signal noise from arms timing out under contention
#
# WeakKeyDictionary so the semaphore is garbage-collected with the loop.
import weakref as _weakref
_RR_LLM_SEM_BY_LOOP: _weakref.WeakKeyDictionary = _weakref.WeakKeyDictionary()


def _get_rr_llm_sem() -> asyncio.Semaphore:
    """Per-loop semaphore capping concurrent bandit-routed RR LLM calls.
    Lazy-creates one per event loop on first access; falls through to
    int(KD_RR_SEM) env at creation (default 8)."""
    loop = asyncio.get_running_loop()
    sem = _RR_LLM_SEM_BY_LOOP.get(loop)
    if sem is None:
        try:
            v = int(os.environ.get("KD_RR_SEM", "8"))
        except (TypeError, ValueError):
            v = 8
        v = max(1, v)
        sem = asyncio.Semaphore(v)
        _RR_LLM_SEM_BY_LOOP[loop] = sem
        logger.info(f"[rr-bandit] LLM semaphore initialized: {v} concurrent")
    return sem


# --------------------------------------------------------------------------- #
# RR per-provider concurrency caps (Wave 1.6 — 2026-06-16)
# --------------------------------------------------------------------------- #
# Per-provider in-flight counter prevents the bandit cascade from over-
# pressuring a single free-tier window (e.g. 4 subagents all picking 4
# different NIM deployments → 4 simultaneous calls × 40 RPM ÷ ~20s each
# = comfortable; but SambaNova at 20 RPM with 60s response times can't
# tolerate even 2 simultaneous). When a provider is at cap, the bandit
# cascade skips to the next ranked arm instead of queueing.
#
# Routing-strategy note: we DON'T change LiteLLM Router's `simple-shuffle`
# to `least-busy` globally because that Router serves Planner/Synth too,
# and their existing per-node semaphores + bandit pinning already control
# concurrency the way that DD ecosystem expects. RR's bandit chain
# BYPASSES Router (calls litellm.acompletion directly with the bandit-
# picked deployment_id), so Router's routing_strategy doesn't apply to
# RR LLM calls anyway. Provider caps below are the RR-specific knob.
_RR_PROVIDER_CAPS: dict[str, int] = {
    "nvidia_nim": 4,  # NIM 40 RPM ÷ ~20s = comfortably 4 in-flight
    "groq":       2,  # Groq 30 RPM peak; tighter cap absorbs bursts
    "cerebras":   2,
    "mistral":    3,
    "gemini":     2,
    "deepseek":   2,
    "sambanova":  2,  # SambaNova free 20 RPM — tightest cap
}

_RR_PROVIDER_INFLIGHT_BY_LOOP: _weakref.WeakKeyDictionary = _weakref.WeakKeyDictionary()


def _get_rr_provider_inflight() -> dict[str, int]:
    """Per-loop dict tracking in-flight RR LLM calls by provider. Each
    bandit-routed call increments before litellm.acompletion + decrements
    in finally, so cascades that skip an at-cap provider see accurate
    state across the parallel subagent fan-out."""
    loop = asyncio.get_running_loop()
    state = _RR_PROVIDER_INFLIGHT_BY_LOOP.get(loop)
    if state is None:
        state = {}
        _RR_PROVIDER_INFLIGHT_BY_LOOP[loop] = state
    return state


# --------------------------------------------------------------------------- #
# Provider entry builders — each returns a LiteLLM model_list item
# --------------------------------------------------------------------------- #
def _groq_entry(group: str, model: str, timeout_s: int = 120) -> dict:
    return {
        "model_name": group,
        "litellm_params": {
            "model":       f"groq/{model}",
            "api_key":     resolve_key("GROQ_API_KEY"),
            "timeout":     timeout_s,
            "max_retries": 0,
        },
    }


def _nim_entry(group: str, model: str, timeout_s: int = 120) -> dict:
    return {
        "model_name": group,
        "litellm_params": {
            "model":       f"nvidia_nim/{model}",
            "api_key":     resolve_key("NVIDIA_API_KEY"),
            "timeout":     timeout_s,
            "max_retries": 0,
        },
    }


def _cerebras_entry(group: str, model: str, timeout_s: int = 120) -> dict:
    return {
        "model_name": group,
        "litellm_params": {
            "model":       f"cerebras/{model}",
            "api_key":     resolve_key("CEREBRAS_API_KEY"),
            "timeout":     timeout_s,
            "max_retries": 0,
        },
    }


def _mistral_entry(group: str, model: str, timeout_s: int = 120) -> dict:
    return {
        "model_name": group,
        "litellm_params": {
            "model":       f"mistral/{model}",
            "api_key":     resolve_key("MISTRAL_API_KEY"),
            "timeout":     timeout_s,
            "max_retries": 0,
        },
    }


def _gemini_entry(group: str, model: str, timeout_s: int = 120) -> dict:
    return {
        "model_name": group,
        "litellm_params": {
            "model":       f"gemini/{model}",
            "api_key":     resolve_key("GOOGLE_API_KEY"),
            "timeout":     timeout_s,
            "max_retries": 0,
        },
    }


def _deepseek_entry(group: str, model: str, timeout_s: int = 120) -> dict:
    return {
        "model_name": group,
        "litellm_params": {
            "model":       f"deepseek/{model}",
            "api_key":     resolve_key("DEEPSEEK_API_KEY"),
            "timeout":     timeout_s,
            "max_retries": 0,
        },
    }


def _sambanova_entry(group: str, model: str, timeout_s: int = 120) -> dict:
    return {
        "model_name": group,
        "litellm_params": {
            "model":       f"sambanova/{model}",
            "api_key":     resolve_key("SAMBANOVA_API_KEY"),
            "timeout":     timeout_s,
            "max_retries": 0,
        },
    }


# --------------------------------------------------------------------------- #
# Static catalogs — fallback when DD_DYNAMIC_CATALOG=0 or discovery fails
# --------------------------------------------------------------------------- #
def _keylm_entries() -> list:
    """Two-deep small-LM rotator for KeyLLM cluster labeling. Both NIM —
    Groq retired llama-3.2-1b-preview; NIM-only matches the embedding rule.
    3B is fallback because NIM 40 RPM was saturated on 28-cluster bursts."""
    return [
        _nim_entry(KEYLM_GROUP, "meta/llama-3.2-1b-instruct", timeout_s = 30),
        _nim_entry(KEYLM_GROUP, "meta/llama-3.2-3b-instruct", timeout_s = 45),
    ]


def _reduce_label_entries() -> list:
    """Non-reasoning rotator for the REDUCE step's labeling + ordering. Order:
    fastest LPU/TPU first, then NIM hybrid-Mamba + gpt-oss + Mistral-Large-3,
    then Mistral direct, then Llama-4 Maverick deep tail. Tighter timeouts
    than dd-all (60-90s) — these calls are structurally short."""
    return [
        _groq_entry(REDUCE_LABEL_GROUP,    "llama-3.3-70b-versatile",                    timeout_s=60),
        _gemini_entry(REDUCE_LABEL_GROUP,  "gemini-3.1-flash-lite",                      timeout_s=60),
        _nim_entry(REDUCE_LABEL_GROUP,     "nvidia/nemotron-3-super-120b-a12b",          timeout_s=90),
        _nim_entry(REDUCE_LABEL_GROUP,     "openai/gpt-oss-120b",                        timeout_s=90),
        _nim_entry(REDUCE_LABEL_GROUP,     "mistralai/mistral-large-3-675b-instruct-2512", timeout_s=90),
        _mistral_entry(REDUCE_LABEL_GROUP, "mistral-large-latest",                      timeout_s=90),
        _mistral_entry(REDUCE_LABEL_GROUP, "mistral-small-latest",                      timeout_s=60),
        _nim_entry(REDUCE_LABEL_GROUP,     "meta/llama-4-maverick-17b-128e-instruct",    timeout_s=90),
    ]


def _synth_entries() -> list:
    """Hybrid reasoning + non-reasoning pool for hierarchical_synth Phase C.
    Reasoning models FIRST (their structured-output completeness is what the
    post-synth audit gate requires); non-reasoning Tier 2/3 absorb cooldown.
    Concurrency cap (DD_LLM_GLOBAL_CONCURRENCY=10) prevents Tier 1 cascade-
    exhaustion via <think> timeouts."""
    return [
        # Tier 1: Frontier reasoning
        _nim_entry(SYNTH_GROUP, "moonshotai/kimi-k2.6",                         timeout_s = 180),
        _nim_entry(SYNTH_GROUP, "z-ai/glm-5.1",                                 timeout_s = 180),
        _nim_entry(SYNTH_GROUP, "minimaxai/minimax-m2.7",                       timeout_s = 180),
        _nim_entry(SYNTH_GROUP, "deepseek-ai/deepseek-v4-flash",                timeout_s = 180),
        # Tier 2: Frontier non-reasoning
        _mistral_entry(SYNTH_GROUP, "mistral-large-latest",                     timeout_s = 120),
        _nim_entry(SYNTH_GROUP, "mistralai/mistral-large-3-675b-instruct-2512", timeout_s = 120),
        _nim_entry(SYNTH_GROUP, "nvidia/nemotron-3-super-120b-a12b",            timeout_s = 120),
        _nim_entry(SYNTH_GROUP, "openai/gpt-oss-120b",                          timeout_s = 120),
        # Tier 3: Deep-tail cooldown absorbers
        _mistral_entry(SYNTH_GROUP, "mistral-medium-latest",                    timeout_s = 120),
        _mistral_entry(SYNTH_GROUP, "mistral-small-latest",                     timeout_s = 90),
        _nim_entry(SYNTH_GROUP, "meta/llama-4-maverick-17b-128e-instruct",      timeout_s = 120),
    ]


def _embed_entries() -> list:
    """Single-entry embedding group — rotating across providers breaks cosine
    geometry within a study. NIM-specific: encoding_format is required;
    input_type passed per call by `embed_via_router_*` (passage vs query)."""
    return [
        {
            "model_name": DD_EMBED_GROUP,
            "litellm_params": {
                "model":           f"nvidia_nim/{DD_EMBED_MODEL_NAME}",
                "api_key":         resolve_key("NVIDIA_API_KEY"),
                "timeout":         120,
                "max_retries":     0,
                "encoding_format": "float",
            },
        },
    ]


# --------------------------------------------------------------------------- #
# Embeddings / judge / rerank
# --------------------------------------------------------------------------- #
def _require_nim_key() -> str:
    """Resolved NVIDIA NIM key or raise an actionable error. NIM is mandatory
    for embeddings + rerank."""
    key = resolve_key("NVIDIA_API_KEY")
    if not key:
        raise RuntimeError(_NIM_REQUIRED_MSG)
    return key


def embed_via_router_sync(
    texts: list[str],
    input_type: str = "passage",
) -> list[list[float]]:
    """Sync batch-embed via dd-embed. Auto-batches at DD_EMBED_BATCH_SIZE.

    `input_type`: "passage" for indexed docs, "query" for short anchor/search
    strings. Asymmetric models (llama-embed-nemotron-8b family) have different
    encoding heads — wrong one costs 3-8 cosine points silently.

    `truncate="END"` is NIM-specific belt-and-suspenders for tokenizer-drift
    edge cases. Upstream token cap is the primary safety; this is the
    server-side net.
    """
    if not texts:
        return []
    _require_nim_key()
    router = _get_router()
    clean = [t if (t and t.strip()) else " " for t in texts]
    out: list[list[float]] = []
    for start in range(0, len(clean), DD_EMBED_BATCH_SIZE):
        batch = clean[start:start + DD_EMBED_BATCH_SIZE]
        with genai_embedding_span_sync(
            request_model = DD_EMBED_GROUP,
            texts         = batch,
            input_type    = input_type,
        ) as span:
            response = router.embedding(
                model = DD_EMBED_GROUP,
                input = batch,
                encoding_format = "float",
                input_type = input_type,
                truncate = "END",
            )
            span.attach_embedding_response(response)
        out.extend(item["embedding"] for item in response["data"])
    if len(out) != len(texts):
        raise RuntimeError(
            f"dd-embed: rotator returned {len(out)} vectors for {len(texts)} inputs"
        )
    return out


async def embed_via_router_async(
    texts: list[str],
    input_type: str = "passage",
    on_batch=None,
) -> list[list[float]]:
    """Async equivalent of embed_via_router_sync. `on_batch(n_done, n_total,
    batch_size)` if provided is fire-and-forget — callback errors are swallowed
    so embedding work always takes precedence."""
    if not texts:
        return []
    _require_nim_key()
    router = _get_router()
    clean = [t if (t and t.strip()) else " " for t in texts]
    total = len(clean)
    out: list[list[float]] = []
    for start in range(0, total, DD_EMBED_BATCH_SIZE):
        batch = clean[start:start + DD_EMBED_BATCH_SIZE]
        async with genai_embedding_span(
            request_model = DD_EMBED_GROUP,
            texts         = batch,
            input_type    = input_type,
        ) as span:
            response = await router.aembedding(
                model = DD_EMBED_GROUP,
                input = batch,
                encoding_format = "float",
                input_type = input_type,
                truncate = "END",
            )
            span.attach_embedding_response(response)
        out.extend(item["embedding"] for item in response["data"])
        if on_batch is not None:
            try:
                await on_batch(
                    n_done = len(out),
                    n_total = total,
                    batch_size = len(batch))
            except Exception:
                pass
    if len(out) != len(texts):
        raise RuntimeError(
            f"dd-embed: rotator returned {len(out)} vectors for {len(texts)} inputs"
        )
    return out


async def chat_judge_async(
    prompt: str,
    max_tokens: int = 8,
    temperature: float = 0.0,
) -> str:
    """Single-shot text classification via dd-all Router (simple-shuffle).
    Prefer `chat_judge_bandit_async` for repeated calls so the bandit can
    learn which deployments are reliable."""
    router = _get_router()
    messages = [{"role": "user", "content": prompt}]
    async with genai_completion_span(
        request_model = GROUP,
        messages      = messages,
        temperature   = temperature,
        max_tokens    = max_tokens,
    ) as span:
        response = await router.acompletion(
            model = GROUP,
            messages = messages,
            temperature = temperature,
            max_tokens = max_tokens,
        )
        span.attach_chat_response(response)
    return (response.choices[0].message.content or "").strip()


async def _redis_for_bandit():
    """Lazily build a Redis client for ParetoBandit reads/writes. None on env-
    misconfig so callers fall back gracefully."""
    if "REDIS_HOST" not in os.environ:
        return None
    host = os.environ["REDIS_HOST"].strip()
    if not host:
        return None
    port = os.environ["REDIS_PORT"].strip() if "REDIS_PORT" in os.environ else "6379"
    password = os.environ["REDIS_PASSWORD"].strip() if "REDIS_PASSWORD" in os.environ else ""
    url = f"redis://:{password}@{host}:{port}" if password else f"redis://{host}:{port}"
    try:
        return redis_aio.from_url(url, socket_connect_timeout = 3.0, socket_timeout = 5.0)
    except Exception:
        return None


def _prune_arm_cooldown() -> None:
    """Drop expired per-arm 429 cooldown entries. Cheap O(N) over a typically
    <10-entry dict."""
    if not _arm_cooldown:
        return
    now = time.monotonic()
    for d in [k for k, exp in _arm_cooldown.items() if exp <= now]:
        _arm_cooldown.pop(d, None)


async def chat_judge_bandit_async(
    prompt: str,
    *,
    max_tokens: int = 8,
    temperature: float = 0.0,
    timeout_s: float = 30.0,
    expected_pattern: str | None = None,
    dd_process: str | None = None,
    candidate_filter=None,
    response_format: dict | None = None,
) -> tuple[str, dict]:
    """Bandit-routed single-shot text classification.

    dd_process: bandit-cell namespace; default "dd-grader". Pass "dd-synth-write"
    for SAWC writer drafts (separate cells + optional heavyweight filter).
    candidate_filter(deployment_id) -> bool excludes BEFORE predict_top_k.
    response_format (json_schema) attaches ONLY to providers in
    _RESPONSE_FORMAT_SAFE_PROVIDERS.

    Cascades top-K ranked deployments via direct `litellm.acompletion(model=...)`
    bypassing Router shuffle. Submits reward after each attempt. Falls back to
    Router-shuffle on infrastructure failure.
    """
    # Rebuild dynamic catalog if BYOK settings moved so the bandit ranks ONLY
    # over enabled∩selected models. Cheap gen check.
    await ensure_dynamic_catalog()
    effective_process = dd_process or _JUDGE_KD_PROCESS
    _prune_arm_cooldown()
    rds = await _redis_for_bandit()
    if rds is None:
        text = await chat_judge_async(
            prompt, 
            max_tokens = max_tokens, 
            temperature = temperature)
        return text, {
            "deployment": "router-shuffle",
            "attempts":   0,
            "latency_s":  None,
            "reward":     None,
            "dd_process": effective_process,
            "fallback":   "no_redis",
        }
    candidates = [e["litellm_params"]["model"] for e in _all_entries_current()]
    if candidate_filter is not None:
        filtered = [c for c in candidates if candidate_filter(c)]
        if filtered:
            candidates = filtered
        # If filter zeros the pool, fall back to the full set rather than 503ing.
    ctx = bandit.make_context_vector(effective_process)
    pattern = re.compile(expected_pattern) if expected_pattern else None
    try:
        ranked = await bandit.predict_top_k(
            effective_process,
            ctx,
            candidates,
            redis = rds,
            k = JUDGE.bandit_top_k,
        )
        # Drop cooling arms from this cascade. Cooldown is in-process (per
        # Celery worker), short, reset on success. If filter empties the
        # cascade, keep the original ranking — better to hit 429 than 503.
        if _arm_cooldown:
            now = time.monotonic()
            filtered = [(d, u, n) for d, u, n in ranked if _arm_cooldown.get(d, 0.0) <= now]
            if filtered:
                if len(filtered) < len(ranked):
                    logger.info(
                        f"[dd-judge-bandit] cooldown filtered "
                        f"{len(ranked) - len(filtered)} arm(s) for {effective_process}"
                    )
                ranked = filtered
    except Exception as e:
        logger.warning(f"[dd-judge-bandit] predict_top_k failed: {e}; falling back to router-shuffle")
        try:
            await rds.aclose()
        except Exception:
            pass
        text = await chat_judge_async(
            prompt, 
            max_tokens = max_tokens, 
            temperature = temperature)
        return text, {
            "deployment": "router-shuffle",
            "attempts":   0,
            "latency_s":  None,
            "reward":     None,
            "fallback":   "predict_failed",
        }
    last_error: str | None = None
    attempts = 0
    messages = [{"role": "user", "content": prompt}]
    async with genai_bandit_cascade_span(dd_process = effective_process) as cascade:
        try:
            for deployment_id, _ucb, _n_obs in ranked:
                attempts += 1
                provider = deployment_id.split("/", 1)[0] if "/" in deployment_id else ""
                api_key = resolve_key(provider_key_env(provider)) or resolve_key("NVIDIA_API_KEY") or ""
                t0 = time.monotonic()
                error_class: str | None = None
                success = False
                schema_valid = False
                response_text = ""
                async with genai_bandit_attempt_span(
                    deployment_id = deployment_id,
                    attempt       = attempts,
                    dd_process    = effective_process,
                    messages      = messages,
                    temperature   = temperature,
                    max_tokens    = max_tokens,
                ) as attempt_span:
                    try:
                        acompletion_kwargs = dict(
                            model = deployment_id,
                            api_key = api_key,
                            messages = messages,
                            temperature = temperature,
                            max_tokens = max_tokens,
                            timeout = timeout_s,
                        )
                        # Attach response_format only for providers that translate it
                        # cleanly. Caller's Pydantic repair loop covers slip-through.
                        if response_format is not None and any(
                            deployment_id.startswith(p) for p in _RESPONSE_FORMAT_SAFE_PROVIDERS
                        ):
                            acompletion_kwargs["response_format"] = response_format
                        response = await litellm.acompletion(**acompletion_kwargs)
                        attempt_span.attach_chat_response(response)
                        response_text = (response.choices[0].message.content or "").strip()
                        success = True
                        if pattern is not None:
                            head = (response_text.upper().split()[0].strip(".,;:!\"'`)")
                                    if response_text else "")
                            schema_valid = bool(pattern.match(head))
                        else:
                            schema_valid = bool(response_text)
                    except Exception as e:
                        error_class = classify_error(e)
                        last_error = f"{type(e).__name__}: {str(e)[:120]}"
                        # 429 → blacklist for the rest of the burst window. Subsequent
                        # cascades skip this arm regardless of dd_process.
                        if error_class == "rate_limit":
                            _arm_cooldown[deployment_id] = time.monotonic() + _ARM_COOLDOWN_S
                    latency_s = float(time.monotonic() - t0)
                    reward = bandit.compose_reward(
                        success = success,
                        schema_valid = schema_valid,
                        latency_s = latency_s,
                        expected_latency_s = JUDGE.expected_latency_s,
                        error_class = error_class,
                    )
                    update_bandit_outcome(
                        attempt_span,
                        latency_s    = latency_s,
                        reward       = reward,
                        error_class  = error_class,
                        schema_valid = schema_valid,
                    )
                try:
                    await bandit.update(
                        deployment_id,
                        effective_process,
                        ctx,
                        reward,
                        redis = rds)
                except Exception:
                    pass
                if success and schema_valid:
                    return response_text, {
                        "deployment": deployment_id,
                        "attempts":   attempts,
                        "latency_s":  latency_s,
                        "reward":     reward,
                        "dd_process": effective_process,
                    }
                # Success but bad schema: return — caller handles unparseable.
                # More model swaps won't fix a schema quirk.
                if success:
                    return response_text, {
                        "deployment":     deployment_id,
                        "attempts":       attempts,
                        "latency_s":      latency_s,
                        "reward":         reward,
                        "schema_invalid": True,
                        "dd_process":     effective_process,
                    }
                # On failure: cascade to next ranked deployment.
            raise RuntimeError(
                f"dd-judge-bandit: all {attempts} ranked deployments failed; "
                f"last error: {last_error}"
            )
        finally:
            cascade.set_total_attempts(attempts)
            try:
                await rds.aclose()
            except Exception:
                pass


async def rerank_via_router_async(
    query: str, documents: list[str], top_n: int | None = None,
) -> list[tuple[int, float]]:
    """Cross-encoder rerank via NIM's hosted rerank API. Returns descending
    (orig_index, relevance_score) pairs. NIM returns raw logits (~[-12, +12]);
    caller thresholds. Direct httpx — NIM's rerank API isn't OpenAI-compat."""
    if not documents:
        return []
    api_key = _require_nim_key()
    model_slug = DD_RERANK_MODEL_NAME.split("/", 1)[-1]
    url = f"{_NIM_RERANK_BASE}/nvidia/{model_slug}/reranking"
    payload = {
        "model":    DD_RERANK_MODEL_NAME,
        "query":    {"text": query},
        "passages": [{"text": d} for d in documents],
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept":        "application/json",
        "Content-Type":  "application/json",
    }
    async with genai_rerank_span(
        request_model = DD_RERANK_MODEL_NAME,
        query         = query,
        documents     = documents,
        system        = system_for_deployment(DD_RERANK_MODEL_NAME),
    ) as span:
        async with httpx.AsyncClient(timeout = 60.0) as client:
            resp = await client.post(url, json = payload, headers = headers)
            resp.raise_for_status()
            data = resp.json()
        rankings = data.get("rankings") or []
        pairs = [(int(r["index"]), float(r["logit"])) for r in rankings]
        if top_n is not None:
            pairs = pairs[:top_n]
        span.attach_rerank_response(pairs)
    return pairs


# --------------------------------------------------------------------------- #
# Static catalog assembly (dd-all)
# --------------------------------------------------------------------------- #
def _rr_strong_entries() -> list:
    """Curated strong-tier pool for the RR DeepAgents orchestrator.

    Wave 1.3 (2026-06-16): expanded 4 → 9 arms to match dd-synth pool
    diversity. With the new bandit-routed chain (`_BanditRoutedRotatorChain`)
    the rotator no longer simple-shuffles — FGTS-VA picks the best arm per
    call, so a wider candidate set means more exploration / lower per-arm
    contention under parallel subagent dispatch. All arms are 120B+ frontier
    tool-callers proven on dd-synth.

    Order matters only for the predict_top_k tie-break (lowest n_obs first).
    Categories:
      - NIM frontier reasoning  (4): glm-5.1, minimax-m2.7, deepseek-v4-flash, kimi-k2.6
      - NIM frontier non-reasoning (3): nemotron-3-super-120b, gpt-oss-120b, mistral-large-3-675b
      - Mistral direct (2): mistral-large, mistral-medium

    Excluded (per [[feedback_free_tier_only]] — strict 100% free, no paid
    SaaS even with a free tier):
      - SambaNova Meta-Llama-3.1-405B  — 2026-06-16: their "free" tier now
        requires a payment method on file ("APIError: SambanovaException -
        A payment method is required"). Effectively paid → dropped.
      - Cerebras llama-3.3-70b         — 2026-06-16: free-tier 404 confirmed
        for the second time (original rr-strong comment flagged it; the
        Wave 2.2 retry attempt hit the same wall). Free tier appears to be
        invite-only at the model slugs we tried; revisit when Cerebras
        clarifies free-tier availability.

    SMALLER arms (17B-49B) stay OUT — phantom-completion failure mode
    observed 2026-06-12.
    """
    return [
        # Tier 1: NIM frontier reasoning (4)
        _nim_entry(RR_STRONG_GROUP,     "moonshotai/kimi-k2.6",                          timeout_s = 120),
        _nim_entry(RR_STRONG_GROUP,     "z-ai/glm-5.1",                                  timeout_s = 120),
        _nim_entry(RR_STRONG_GROUP,     "minimaxai/minimax-m2.7",                        timeout_s = 120),
        _nim_entry(RR_STRONG_GROUP,     "deepseek-ai/deepseek-v4-flash",                 timeout_s = 120),
        # Tier 2: NIM frontier non-reasoning (3)
        _nim_entry(RR_STRONG_GROUP,     "nvidia/nemotron-3-super-120b-a12b",             timeout_s = 120),
        _nim_entry(RR_STRONG_GROUP,     "openai/gpt-oss-120b",                           timeout_s = 120),
        _nim_entry(RR_STRONG_GROUP,     "mistralai/mistral-large-3-675b-instruct-2512",  timeout_s = 120),
        # Tier 3: Mistral direct (2)
        _mistral_entry(RR_STRONG_GROUP, "mistral-large-latest",                          timeout_s = 120),
        _mistral_entry(RR_STRONG_GROUP, "mistral-medium-latest",                         timeout_s = 120),
    ]


def _all_entries() -> list:
    """Static dd-all catalog — fallback when DD_DYNAMIC_CATALOG=0 or discovery
    fails. Strict benchmark order (2026-04-24 refresh). Disabled entries kept
    as comments so the rationale survives next time someone wonders why X is
    missing."""
    return [
        _nim_entry(GROUP, "z-ai/glm-5.1",                                  timeout_s = 120),
        _nim_entry(GROUP, "minimaxai/minimax-m2.7",                        timeout_s = 120),
        _nim_entry(GROUP, "moonshotai/kimi-k2.6",                          timeout_s = 120),
        _gemini_entry(GROUP, "gemini-3-flash-preview",                     timeout_s = 120),
        _nim_entry(GROUP, "qwen/qwen3.5-397b-a17b",                        timeout_s = 120),
        _nim_entry(GROUP, "deepseek-ai/deepseek-v4-flash",                 timeout_s = 120),
        _nim_entry(GROUP, "minimaxai/minimax-m2.7",                        timeout_s = 120),
        _nim_entry(GROUP, "nvidia/nemotron-3-super-120b-a12b",             timeout_s = 120),
        _nim_entry(GROUP, "deepseek-ai/deepseek-v4-flash",                 timeout_s = 120),
        _gemini_entry(GROUP, "gemini-3.1-flash-lite",                      timeout_s = 90),
        _nim_entry(GROUP, "openai/gpt-oss-120b",                           timeout_s = 120),
        _gemini_entry(GROUP, "gemini-2.5-flash",                           timeout_s = 60),
        _mistral_entry(GROUP, "mistral-small-latest",                      timeout_s = 120),
        _mistral_entry(GROUP, "magistral-medium-latest",                   timeout_s = 120),
        _mistral_entry(GROUP, "mistral-large-latest",                      timeout_s = 120),
        _nim_entry(GROUP, "mistralai/mistral-large-3-675b-instruct-2512",  timeout_s = 120),
        _mistral_entry(GROUP, "devstral-medium-latest",                    timeout_s = 120),
        _mistral_entry(GROUP, "mistral-medium-latest",                     timeout_s = 60),
        _mistral_entry(GROUP, "magistral-small-latest",                    timeout_s = 120),
        _nim_entry(GROUP, "meta/llama-4-maverick-17b-128e-instruct",       timeout_s = 120),
    ]


# --------------------------------------------------------------------------- #
# BYOK selection filter + rotator invalidation
# --------------------------------------------------------------------------- #
def _read_selection(force: bool = False) -> dict:
    """The user's BYOK selection blob (TTL-cached in the store; force=True
    bypasses the cache for the dynamic-catalog rebuild). {} on error."""
    try:
        from domains.llm.credentials import get_store
        return get_store().read_settings(force = force) or {}
    except Exception:
        return {}


def _apply_selection_filter(entries: list[dict]) -> list[dict]:
    """Trim chat entries to the user's enabled providers + selected models.
    No selection stored → entries unchanged. No-empty guard: a selection that
    would empty the pool is ignored (logged) so the rotator stays alive."""
    sel = _read_selection()
    if not sel:
        return entries
    out = [e for e in entries if selection_allows(*entry_provider_and_model(e), sel)]
    if not out:
        logger.warning(
            "[llm-chain] user selection emptied the chat catalog — ignoring "
            "filter (no-empty guard)"
        )
        return entries
    return out


# --------------------------------------------------------------------------- #
# Account-inaccessibility hard blocklist — drop arms known to return NIM 404
# ("Function not found for account ...") on every call. LiteLLM 1.83's
# RetryPolicy has no NotFoundErrorRetries field, so a Router pick on these
# arms TERMINATES the cascade rather than falling through. Hard-filtering
# them BEFORE the Router sees model_list (and before predict_top_k builds
# candidates) eliminates the failure mode.
#
# Add entries here when logs show a model's `litellm.NotFoundError - 404 -
# Function '...': Not found for account '...'`. The model is in the dynamic
# catalog (NIM's listing API exposes it) but our free-tier key can't invoke
# it. Strip from EVERY pool — no model is account-accessible for one
# workload class only.
#
# TODO: retire this list when we bump LiteLLM past 1.85 (the version that
# ships `NotFoundErrorRetries` in RetryPolicy) — at that point the Router
# can cascade past 404 on its own.
# --------------------------------------------------------------------------- #
_ACCOUNT_INACCESSIBLE_BLOCKLIST: frozenset[str] = frozenset({
    # 2026-06-12: observed during RR scan smoke test. NIM returned 404 with
    # function-not-found-for-account on these arms. Dynamic catalog discovers
    # them (NIM listing API exposes them) but our free-tier key can't invoke
    # them. Hard-block here so the Router's model_list never includes them.
    "nvidia/llama-3.1-nemotron-ultra-253b-v1",
    "nvidia/nemotron-4-340b-instruct",
})


# Runtime-learned inaccessibility — augments the static blocklist with
# models observed to 404 during this process's lifetime. Persists for the
# worker's lifetime; resets on process restart. The retry wrapper below
# adds entries on the fly when it catches `litellm.NotFoundError`.
_RUNTIME_INACCESSIBLE_MODELS: set[str] = set()


def mark_inaccessible(model_id: str) -> None:
    """Add a model to the runtime blocklist + reset the Router so the
    next call rebuilds model_list without the bad arm. Idempotent."""
    if not model_id or model_id in _RUNTIME_INACCESSIBLE_MODELS:
        return
    _RUNTIME_INACCESSIBLE_MODELS.add(model_id)
    logger.warning(
        f"[llm-chain] runtime auto-blocklist: {model_id!r} (NIM 404 observed)"
    )
    # Drop the Router cache so the next acompletion rebuilds model_list
    # without this arm. bump_gen=False — this is a per-process learning,
    # not a user-driven settings change to propagate cluster-wide.
    reset_rotator(bump_gen=False)


def _apply_inaccessibility_filter(entries: list[dict]) -> list[dict]:
    """Drop arms in `_ACCOUNT_INACCESSIBLE_BLOCKLIST` ∪
    `_RUNTIME_INACCESSIBLE_MODELS`. Substring match on the
    `litellm_params.model` string so provider-prefixed entries
    (e.g. `nvidia_nim/<id>`) match correctly. No-empty guard."""
    blocklist = _ACCOUNT_INACCESSIBLE_BLOCKLIST | frozenset(_RUNTIME_INACCESSIBLE_MODELS)
    if not blocklist:
        return entries
    out = [
        e for e in entries
        if not any(bad in e["litellm_params"]["model"] for bad in blocklist)
    ]
    if not out:
        logger.warning(
            "[llm-chain] inaccessibility blocklist would empty the pool — "
            "ignoring filter (no-empty guard)"
        )
        return entries
    n_dropped = len(entries) - len(out)
    if n_dropped:
        logger.info(
            f"[llm-chain] inaccessibility blocklist dropped {n_dropped} "
            f"arm(s) (account-404 risk; static={len(_ACCOUNT_INACCESSIBLE_BLOCKLIST)}, "
            f"runtime={len(_RUNTIME_INACCESSIBLE_MODELS)})"
        )
    return out


# --------------------------------------------------------------------------- #
# Auto-retry router subclass — catches `litellm.NotFoundError`, parses the
# failing model out of the error message, calls mark_inaccessible() to
# auto-learn, refreshes `self.router` against the rebuilt model_list, and
# retries the same input. After at most N inaccessible arms in the catalog
# the call succeeds (assuming any accessible arm remains).
#
# Inheritance choice — subclassing ChatLiteLLMRouter (which IS a langchain
# BaseChatModel) rather than wrapping it. DeepAgents v0.6's `resolve_model`
# does `isinstance(model, BaseChatModel)` and returns the object unchanged on
# True; a non-BaseChatModel wrapper falls through to the string-path which
# calls `.count(":")` on it → AttributeError. The subclass passes the check.
#
# WHY this lives in our code (not at the Router): LiteLLM 1.83.13's
# RetryPolicy has NO NotFoundErrorRetries field, so a Router pick on a
# 404 arm terminates the cascade. Future LiteLLM (1.85+) ships the field;
# when we bump the pin, this subclass can shed back to a simpler Router
# config.
# --------------------------------------------------------------------------- #
import re as _re
_MODEL_RE = _re.compile(r"litellm\.acompletion\(model=([^)\s]+)\)")
# Backup: NIM error embeds the model differently — match `nvidia_nim/<...>`
_NIM_PREFIX_RE = _re.compile(r"nvidia_nim/([\w./-]+)")
# Cerebras + some other providers report inaccessible models as
# "Model <name> does not exist or you do not have access to it" rather
# than the LiteLLM log-line format. Catch that prose too.
_DOES_NOT_EXIST_RE = _re.compile(
    r"Model\s+([\w./-]+)\s+does\s+not\s+exist",
    _re.IGNORECASE,
)


_GROUP_NAMES: frozenset[str] = frozenset({
    "dd-all", "rr-strong", "dd-synth", "dd-reduce-label",
    "dd-keylm", "dd-embed",
})


def _extract_model_from_error(err_text: str, exc: BaseException | None = None) -> str | None:
    """Pull the failing model id from a litellm exception text. Returns
    bare model (e.g. `nvidia/nemotron-4-340b-instruct` or `llama3.1-70b`)
    so it round-trips through `_apply_inaccessibility_filter`'s substring
    match.

    Strategy (best → worst):
      1. Exception attributes (`.model`, `.llm_provider`) — most reliable
         when LiteLLM populates them, BUT for Router calls the .model attr
         often holds the model-GROUP name (`dd-all`) which we must reject.
      2. `litellm.acompletion(model=<provider/id>)` log-line format
      3. `Model <name> does not exist` prose (Cerebras + similar)
      4. Bare `nvidia_nim/<id>` substring match
    """
    if exc is not None:
        for attr in ("model", "llm_provider"):
            val = getattr(exc, attr, None)
            if isinstance(val, str) and val and val not in _GROUP_NAMES:
                # Strip provider prefix if present so it matches catalog form
                if val.startswith("nvidia_nim/"):
                    return val.split("nvidia_nim/", 1)[1]
                return val
    m = _MODEL_RE.search(err_text)
    if m:
        raw = m.group(1).strip().strip("'\"")
        if raw.startswith("nvidia_nim/"):
            return raw.split("nvidia_nim/", 1)[1]
        return raw
    m = _DOES_NOT_EXIST_RE.search(err_text)
    if m:
        return m.group(1).strip().strip("'\".,")
    m2 = _NIM_PREFIX_RE.search(err_text)
    return m2.group(1) if m2 else None


_MAX_NOTFOUND_RETRIES = 6   # 6 dead arms before we give up


def _flatten_thinking_content(messages):
    """Flatten list-shaped content on AIMessage, ToolMessage, and
    HumanMessage so non-list-tolerant providers don't reject the request.

    Background: multiple message classes can carry structured content
    blocks that some provider APIs reject:

      - AIMessage with `[{type:thinking,...}, {type:text,...}]`
        (qwen-thinking, deepseek-v4, claude-extended-thinking, …)
      - ToolMessage with `[{type:text, text:...}, ...]` returned by an
        MCP tool — observed 2026-06-12 with `messages.5.tool.content.str:
        Input should be a valid string` from cerebras/gpt-oss-120b
      - HumanMessage with multimodal content (rare in RR)

    On the next-turn request, providers like Cerebras / Mistral / Groq
    declare `content: str` per message and reject the list with a 400.
    Across the cascade every non-list-tolerant arm fails → empty
    generations → IndexError at langchain_core/chat_models.py:508.

    Fix: walk messages, convert list-content to a flat string:
      - keep `{type: text}` block text (concatenated)
      - drop `{type: thinking}` / `{type: reasoning}` / `{type:
        redacted_thinking}` blocks
      - tool_calls / tool_call_id / non-content attrs survive on the
        rebuilt message — the model's action / linkage is preserved

    LiteLLM's own `modify_params=True` handles orphaned tool_calls +
    empty content; it does NOT strip list-content. This helper bridges
    the gap until langchain-litellm picks up the responsibility.
    """
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
    out = []
    for m in messages:
        if not isinstance(m.content, list):
            out.append(m)
            continue
        texts: list[str] = []
        for block in m.content:
            if isinstance(block, dict):
                btype = block.get("type")
                if btype == "text":
                    t = block.get("text") or ""
                    if t:
                        texts.append(t)
                # thinking / reasoning / redacted_thinking / image / tool_use:
                # drop. (Image content in RR's tool results is exceedingly
                # rare; if we ever surface vision, flag here.)
            elif isinstance(block, str):
                if block:
                    texts.append(block)
        new_content = "\n".join(texts).strip()

        if isinstance(m, AIMessage):
            kept = {
                k: getattr(m, k)
                for k in ("tool_calls", "tool_call_chunks", "response_metadata",
                          "additional_kwargs", "id", "name", "usage_metadata",
                          "invalid_tool_calls")
                if hasattr(m, k)
            }
            out.append(AIMessage(content=new_content, **kept))
        elif isinstance(m, ToolMessage):
            # tool_call_id is REQUIRED — it links the tool result back to
            # the AIMessage's tool_calls entry. Other fields are best-effort.
            kept = {
                k: getattr(m, k)
                for k in ("tool_call_id", "name", "id", "status",
                          "artifact", "additional_kwargs")
                if hasattr(m, k)
            }
            out.append(ToolMessage(content=new_content, **kept))
        elif isinstance(m, HumanMessage):
            kept = {
                k: getattr(m, k)
                for k in ("id", "name", "additional_kwargs")
                if hasattr(m, k)
            }
            out.append(HumanMessage(content=new_content, **kept))
        else:
            # Unknown subclass — pass through unchanged rather than risk
            # constructing it wrong. Add a branch when a new case arises.
            out.append(m)
    return out


class _RotatorAutoRetryRouter(ChatLiteLLMRouter):
    """ChatLiteLLMRouter subclass with three cross-provider survival fixes:

    1. **Thinking-block flatten** (every call) — `_flatten_thinking_content`
       strips reasoning-model list-content from message history so the next
       cascade arm doesn't 400 on `content: str` validation.

    2. **NotFoundError auto-learn** — catches `litellm.NotFoundError`,
       parses the failing model from the error, calls `mark_inaccessible()`
       (runtime blocklist + Router reset), refreshes `self.router`, retries.
       Up to `_MAX_NOTFOUND_RETRIES` consecutive dead arms.

    3. **Real-deployment surfacing** (2026-06-16) — overrides
       `_create_chat_result` to store the LiteLLM Router's resolved
       deployment id (e.g. `nvidia_nim/openai/gpt-oss-120b`) in
       `AIMessage.response_metadata["model_name"]` + `llm_output["model_name"]`
       instead of the request group alias (`rr-strong` / `dd-all` / …).
       Upstream langchain-litellm's `_create_chat_result` builds these
       fields from `self.model` (the group), losing the deployment that
       actually answered. RR's per-model counter relies on knowing the
       deployment; this surfacing is read-only (no global state mutation,
       no callback registration → no msgpack regression).
    """

    def _create_chat_result(self, response, **params):
        """Augment the parent's ChatResult with the real deployment id.

        Parent stores `self.model` (group alias) as `model_name`. We
        prefer `response["model"]` — the LiteLLM Router populates this
        with the deployment that actually answered. Falls back to the
        parent's value when the response shape is unusual (e.g. local
        mock, streaming first-chunk before model lands).
        """
        result = super()._create_chat_result(response, **params)
        try:
            real_model = None
            if isinstance(response, dict):
                real_model = response.get("model")
            else:
                real_model = getattr(response, "model", None)
            if isinstance(real_model, str) and real_model:
                # Update llm_output (the LLMResult-level field consumed
                # by langchain on_llm_end).
                if isinstance(result.llm_output, dict):
                    result.llm_output["model_name"] = real_model
                # Update each generation's AIMessage.response_metadata
                # so per-message readers (drawer, traces) also see the
                # deployment instead of the group alias.
                for gen in result.generations or []:
                    msg = getattr(gen, "message", None)
                    if msg is None:
                        continue
                    rm = getattr(msg, "response_metadata", None)
                    if isinstance(rm, dict):
                        rm["model_name"] = real_model
                    else:
                        # Defensively replace with a fresh dict.
                        try:
                            msg.response_metadata = {"model_name": real_model}
                        except Exception:
                            pass
        except Exception:
            # Best-effort augmentation; never break the call path.
            pass
        return result

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        messages = _flatten_thinking_content(messages)
        last_err: Exception | None = None
        for attempt in range(_MAX_NOTFOUND_RETRIES):
            try:
                result = await super()._agenerate(
                    messages, stop=stop, run_manager=run_manager, **kwargs,
                )
            except Exception as e:
                # Broadened 2026-06-12: `is_eol_error` covers `NotFoundError`
                # (404) AND the 410 / "end of life" / "deprecated" paths the
                # original NotFoundError-only catch missed. Same recovery
                # shape — extract, mark inaccessible, reshuffle, retry.
                if not (isinstance(e, litellm.NotFoundError) or is_eol_error(e)):
                    raise
                last_err = e
                model = _extract_model_from_error(str(e), exc=e)
                if model:
                    mark_inaccessible(model)
                else:
                    # Can't identify the specific deployment (NIM hides the
                    # model name behind a function UUID in `str(e)`). Force
                    # a Router reshuffle so the next attempt's simple-shuffle
                    # likely picks a different deployment. LiteLLM Router's
                    # `allowed_fails=3` per-deployment cooldown will take the
                    # bad arm offline after a few cycles.
                    logger.warning(
                        f"[rotator-retry] EOL-class error on attempt "
                        f"{attempt+1} from unidentified deployment; forcing "
                        f"Router reshuffle"
                    )
                self.router = _get_router()
                continue
            # Empty generations recovery — provider returned 200 OK but content
            # was empty / unparseable. langchain_core/chat_models.py:508 indexes
            # `generations[0][0]` and crashes; we treat it as a soft failure and
            # retry against a different deployment. Common causes:
            # (a) Gemini content-policy filter returns empty,
            # (b) langchain-litellm parse glitch on tool-call response,
            # (c) provider returned `{"choices":[]}` silently.
            if not result.generations or not result.generations[0]:
                logger.warning(
                    f"[rotator-retry] empty generations on attempt {attempt+1}; "
                    f"forcing deployment reshuffle"
                )
                last_err = RuntimeError("empty generations from rotator")
                # Reset Router so simple-shuffle re-picks; the bandit's
                # cool-down counter will demote the empty-returning arm.
                self.router = _get_router()
                continue
            return result
        raise last_err if last_err else RuntimeError(
            "[rotator-retry] exhausted without specific error"
        )

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        messages = _flatten_thinking_content(messages)
        last_err: Exception | None = None
        for attempt in range(_MAX_NOTFOUND_RETRIES):
            try:
                result = super()._generate(
                    messages, stop=stop, run_manager=run_manager, **kwargs,
                )
            except Exception as e:
                # Mirror of `_agenerate`'s broadened EOL catch.
                if not (isinstance(e, litellm.NotFoundError) or is_eol_error(e)):
                    raise
                last_err = e
                model = _extract_model_from_error(str(e), exc=e)
                if model:
                    mark_inaccessible(model)
                else:
                    logger.warning(
                        f"[rotator-retry] EOL-class error on attempt "
                        f"{attempt+1} from unidentified deployment; forcing "
                        f"Router reshuffle"
                    )
                self.router = _get_router()
                continue
            if not result.generations or not result.generations[0]:
                logger.warning(
                    f"[rotator-retry] empty generations on attempt {attempt+1}; "
                    f"forcing deployment reshuffle"
                )
                last_err = RuntimeError("empty generations from rotator")
                self.router = _get_router()
                continue
            return result
        raise last_err if last_err else RuntimeError(
            "[rotator-retry] exhausted without specific error"
        )


class _BanditRoutedRotatorChain(_RotatorAutoRetryRouter):
    """RR-only: replaces LiteLLM Router's simple-shuffle with FGTS-VA bandit
    selection per LLM turn (Wave 1.2 — 2026-06-16).

    Mirrors `chat_judge_bandit_async`'s cascade brain (the same one Planner
    and Synth use for every node-level LLM call) but preserves tool_calls so
    DeepAgents subagent loops work unchanged. Each `_agenerate` call:

      1. `bandit.predict_top_k("rr-strong", ctx, candidates)` → ranked deployments
      2. Drops arms in `_arm_cooldown` window (429 budget honored)
      3. Cascades: `litellm.acompletion(model=deployment_id, tools=…)` direct
      4. On success: builds ChatResult via parent's `_create_chat_result`
         (already surfaces the real deployment id into `response_metadata`),
         issues a positive `bandit.update` graded by latency, returns
      5. On failure: classifies error, applies 429 cooldown, issues negative
         `bandit.update`, advances cascade
      6. On bandit-arm exhaustion OR Redis unavailable: falls back to parent's
         simple-shuffle path so a Redis blip never kills the agent

    Gated by env `KD_RR_BANDIT_CHAT` at construction time (the factory
    `build_rr_strong_chain_bandit` instantiates this class; baseline
    `build_rr_strong_chain` keeps the parent class for instant rollback).
    """

    # Separate bandit cell from dd-* processes so RR rewards/penalties don't
    # leak into DD step scoring (and vice versa).
    _RR_DD_PROCESS = RR_STRONG_GROUP   # "rr-strong"

    # Latency floor used by compose_reward — calibrated against the rr-strong
    # pool's 120B+ arms. A 30s call earns near-max reward; a 90s call (the
    # provider timeout edge) earns near-zero. Tunable via env.
    _RR_EXPECTED_LATENCY_S: float = 30.0

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        # Use the parent's thinking-content flatten + EOL-retry path as the
        # safety net. Bandit selection wraps it: pick a deployment per call
        # via FGTS-VA + tool_calls passthrough; fall back to simple-shuffle
        # when bandit infra is unavailable or all ranked arms fail.
        #
        # Wave 1.5: per-loop semaphore caps concurrent bandit-routed calls
        # to KD_RR_SEM (default 8). With 4 parallel deep_read subagents +
        # 1 orchestrator + occasional synthesis = up to ~6 concurrent calls
        # at peak, so 8 absorbs orchestrator-side bursts without queueing.
        messages = _flatten_thinking_content(messages)
        _prune_arm_cooldown()

        async with _get_rr_llm_sem():
            return await self._agenerate_inner(
                messages, stop=stop, run_manager=run_manager, **kwargs,
            )

    async def _agenerate_inner(
        self, messages, stop=None, run_manager=None, **kwargs,
    ):
        """Bandit cascade body — split out so `_agenerate` can wrap with
        the per-loop concurrency semaphore."""
        from langchain_core.messages.utils import convert_to_openai_messages

        rds = await _redis_for_bandit()
        if rds is None:
            # No bandit state available; honor baseline behavior.
            return await super()._agenerate(
                messages, stop=stop, run_manager=run_manager, **kwargs,
            )

        try:
            entries = _rr_strong_entries_current()
            if not entries:
                return await super()._agenerate(
                    messages, stop=stop, run_manager=run_manager, **kwargs,
                )
            candidates = [e["litellm_params"]["model"] for e in entries]
            ctx = bandit.make_context_vector(self._RR_DD_PROCESS)
            try:
                ranked = await bandit.predict_top_k(
                    self._RR_DD_PROCESS,
                    ctx,
                    candidates,
                    redis = rds,
                    k     = len(candidates),
                )
            except Exception as e:
                logger.warning(
                    f"[rr-bandit] predict_top_k failed: "
                    f"{type(e).__name__}: {e}; falling back to simple-shuffle"
                )
                return await super()._agenerate(
                    messages, stop=stop, run_manager=run_manager, **kwargs,
                )

            # Drop cooled-down arms — 429 budget is shared with chat_judge_bandit
            if _arm_cooldown:
                now = time.monotonic()
                live = [
                    (d, s, n) for d, s, n in ranked
                    if _arm_cooldown.get(d, 0.0) <= now
                ]
                if live and len(live) < len(ranked):
                    logger.info(
                        f"[rr-bandit] cooldown dropped "
                        f"{len(ranked) - len(live)} of {len(ranked)} arms"
                    )
                if live:
                    ranked = live

            if not ranked:
                return await super()._agenerate(
                    messages, stop=stop, run_manager=run_manager, **kwargs,
                )

            # Convert langchain messages → OpenAI dict once. The cascade reuses
            # the same conversion across all ranked arms.
            try:
                oai_messages = convert_to_openai_messages(messages)
            except Exception as e:
                logger.warning(
                    f"[rr-bandit] message conversion failed: "
                    f"{type(e).__name__}: {e}; falling back to simple-shuffle"
                )
                return await super()._agenerate(
                    messages, stop=stop, run_manager=run_manager, **kwargs,
                )

            # Tool binding flows via kwargs (BaseChatModel.bind_tools injects
            # `tools` / `tool_choice` into _agenerate kwargs).
            tools          = kwargs.get("tools")
            tool_choice    = kwargs.get("tool_choice")
            temperature    = kwargs.get("temperature", getattr(self, "temperature", 0.0))
            max_tokens     = kwargs.get("max_tokens")
            response_format = kwargs.get("response_format")
            # Per-entry timeouts from the catalog — falls back to 120s.
            timeout_by_id  = {
                e["litellm_params"]["model"]: e["litellm_params"].get("timeout", 120)
                for e in entries
            }

            last_err: Exception | None = None
            attempts = 0
            inflight = _get_rr_provider_inflight()
            for deployment_id, _score, _n_obs in ranked:
                provider = (
                    deployment_id.split("/", 1)[0] if "/" in deployment_id else ""
                )
                # Wave 1.6: per-provider in-flight cap — skip this arm if its
                # provider already has cap calls running. Bandit cascade
                # advances to the next ranked deployment; provider load
                # naturally spreads across the cap-1 alternatives.
                provider_cap = _RR_PROVIDER_CAPS.get(provider, 8)
                if inflight.get(provider, 0) >= provider_cap:
                    logger.debug(
                        f"[rr-bandit] {deployment_id} skipped — provider "
                        f"{provider!r} at cap {provider_cap}"
                    )
                    continue
                attempts += 1
                api_key = (
                    resolve_key(provider_key_env(provider))
                    or resolve_key("NVIDIA_API_KEY")
                    or ""
                )
                deployment_timeout = float(timeout_by_id.get(deployment_id, 120))
                t0 = time.monotonic()
                inflight[provider] = inflight.get(provider, 0) + 1
                try:
                    acompletion_kwargs: dict = dict(
                        model       = deployment_id,
                        api_key     = api_key,
                        messages    = oai_messages,
                        temperature = temperature,
                        timeout     = deployment_timeout,
                    )
                    if max_tokens is not None:
                        acompletion_kwargs["max_tokens"] = max_tokens
                    if tools is not None:
                        acompletion_kwargs["tools"] = tools
                    if tool_choice is not None:
                        acompletion_kwargs["tool_choice"] = tool_choice
                    if stop is not None:
                        acompletion_kwargs["stop"] = stop
                    # response_format honored only on providers that translate
                    # it cleanly — mirrors chat_judge_bandit_async's policy so
                    # we don't 400 mid-cascade on a provider that ignores it.
                    if response_format is not None and any(
                        deployment_id.startswith(p)
                        for p in _RESPONSE_FORMAT_SAFE_PROVIDERS
                    ):
                        acompletion_kwargs["response_format"] = response_format
                        # Wave 2.1 (2026-06-16): on NIM, ALSO attach
                        # nvext.guided_json so XGrammar enforces the
                        # schema at decode time (eliminates malformed
                        # JSON BEFORE it leaves the model). Gated by
                        # KD_RR_GUIDED_JSON (default ON). The grammar
                        # is the response_format's json_schema if shaped
                        # like OpenAI's {type: json_schema, json_schema:
                        # {schema: {...}}}; otherwise the raw payload.
                        if (
                            provider == "nvidia_nim"
                            and os.environ.get(
                                "KD_RR_GUIDED_JSON", "true"
                            ).strip().lower() not in ("0", "false", "no", "off")
                            and isinstance(response_format, dict)
                        ):
                            try:
                                schema = response_format.get("json_schema", {})
                                if isinstance(schema, dict):
                                    schema = schema.get("schema", schema)
                                if isinstance(schema, dict) and schema:
                                    acompletion_kwargs["extra_body"] = {
                                        "nvext": {"guided_json": schema}
                                    }
                            except Exception:
                                # Best-effort — never fail the call over a
                                # nvext annotation.
                                pass
                    response = await litellm.acompletion(**acompletion_kwargs)
                    latency_s = float(time.monotonic() - t0)

                    # Build the ChatResult via the parent's override — it
                    # surfaces the real deployment id (response.model) into
                    # AIMessage.response_metadata["model_name"] so the
                    # per-model counter shows nvidia_nim/openai/gpt-oss-120b
                    # instead of the group alias.
                    #
                    # langchain-litellm's `_create_chat_result` (line 35,
                    # litellm_router.py) does `params["metadata"]` — KeyError
                    # if absent. The Router's `_prepare_params_for_router`
                    # injects that key on the normal path; since we bypass
                    # Router and call `litellm.acompletion` direct, we have
                    # to provide it ourselves. Empty dict is sufficient.
                    result = self._create_chat_result(response, metadata={})

                    # Empty-generations guard — Gemini policy-filter / parse
                    # glitches return 200 OK with no content. Treat as failure.
                    if not result.generations or not result.generations[0]:
                        last_err = RuntimeError(
                            f"empty generations from {deployment_id} "
                            f"(latency_s={latency_s:.2f})"
                        )
                        try:
                            await bandit.update(
                                deployment_id, self._RR_DD_PROCESS,
                                ctx, 0.0, redis = rds,
                            )
                        except Exception:
                            pass
                        logger.warning(
                            f"[rr-bandit] {deployment_id} empty generations; "
                            f"cascading"
                        )
                        continue

                    reward = bandit.compose_reward(
                        success            = True,
                        schema_valid       = True,
                        latency_s          = latency_s,
                        expected_latency_s = self._RR_EXPECTED_LATENCY_S,
                        error_class        = None,
                    )
                    try:
                        await bandit.update(
                            deployment_id, self._RR_DD_PROCESS,
                            ctx, reward, redis = rds,
                        )
                    except Exception:
                        pass
                    logger.debug(
                        f"[rr-bandit] {deployment_id} → ok "
                        f"(latency_s={latency_s:.2f}, attempt={attempts}, "
                        f"reward={reward:.3f})"
                    )
                    return result

                except Exception as e:
                    error_class = classify_error(e)
                    last_err    = e
                    latency_s   = float(time.monotonic() - t0)
                    if error_class == "rate_limit":
                        _arm_cooldown[deployment_id] = (
                            time.monotonic() + _ARM_COOLDOWN_S
                        )
                    reward = bandit.compose_reward(
                        success            = False,
                        schema_valid       = False,
                        latency_s          = latency_s,
                        expected_latency_s = self._RR_EXPECTED_LATENCY_S,
                        error_class        = error_class,
                    )
                    try:
                        await bandit.update(
                            deployment_id, self._RR_DD_PROCESS,
                            ctx, reward, redis = rds,
                        )
                    except Exception:
                        pass
                    logger.info(
                        f"[rr-bandit] {deployment_id} → {error_class}: "
                        f"{type(e).__name__}; cascading"
                    )
                    continue
                finally:
                    # Wave 1.6: release this provider's in-flight slot
                    # whether the call succeeded (return), failed
                    # (continue), or skipped via empty-generations guard
                    # (continue). Underflow guard is paranoia.
                    inflight[provider] = max(
                        0, inflight.get(provider, 0) - 1
                    )

            # All bandit-ranked arms failed — fall back to parent simple-shuffle
            # (which still has its own EOL-retry + empty-gen guards).
            logger.warning(
                f"[rr-bandit] all {attempts} ranked arms failed (last: "
                f"{type(last_err).__name__ if last_err else 'None'}); "
                f"falling back to simple-shuffle Router"
            )
            return await super()._agenerate(
                messages, stop=stop, run_manager=run_manager, **kwargs,
            )

        finally:
            try:
                await rds.aclose()
            except Exception:
                pass

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        """Sync path — DeepAgents always uses async, but BaseChatModel requires
        this method. Defers to the parent's simple-shuffle which already has
        retry + empty-generations guards. Bandit selection only applies to
        async (the actual hot path)."""
        return super()._generate(
            messages, stop=stop, run_manager=run_manager, **kwargs,
        )


def _redis_sync_conn():
    """Sync Redis client for the settings-gen counter. None on env-misconfig."""
    if "REDIS_HOST" not in os.environ:
        return None
    host = os.environ["REDIS_HOST"].strip()
    if not host:
        return None
    import redis as _redis_sync
    port = int(os.environ["REDIS_PORT"].strip()) if "REDIS_PORT" in os.environ else 6379
    password = os.environ["REDIS_PASSWORD"].strip() if "REDIS_PASSWORD" in os.environ else ""
    try:
        return _redis_sync.Redis(
            host = host,
            port = port,
            password = password or None,
            socket_timeout = 2,
            socket_connect_timeout = 2,
        )
    except Exception:
        return None


def _read_settings_gen() -> int:
    """Throttled sync read of the Redis settings-generation counter."""
    global _settings_gen_cache, _settings_gen_read_at
    now = time.monotonic()
    if _settings_gen_read_at and (now - _settings_gen_read_at) < _GEN_THROTTLE_S:
        return _settings_gen_cache
    _settings_gen_read_at = now
    r = _redis_sync_conn()
    if r is None:
        return _settings_gen_cache
    try:
        v = r.get(_SETTINGS_GEN_REDIS_KEY)
        _settings_gen_cache = int(v) if v else 0
    except Exception:
        pass  # Redis blip → keep last known gen; never block the hot path
    finally:
        try:
            r.close()
        except Exception:
            pass
    return _settings_gen_cache


def reset_rotator(*, bump_gen: bool = True) -> int:
    """Drop the cached Router + pinned-chain caches so the next build re-reads
    provider keys (resolve_key) + the user selection. INCRs the Redis generation
    so other processes rebuild on their next access. Returns the new gen."""
    global _router_instance, _settings_gen_local, _settings_gen_cache, _settings_gen_read_at
    _router_instance = None
    _pinned_chain_cache.clear()
    _pinned_to_parent.clear()
    new_gen = _settings_gen_local
    if bump_gen:
        r = _redis_sync_conn()
        if r is not None:
            try:
                new_gen = int(r.incr(_SETTINGS_GEN_REDIS_KEY))
            except Exception as e:
                logger.warning("[llm-chain] settings-gen bump failed: %s", e)
            finally:
                try:
                    r.close()
                except Exception:
                    pass
    _settings_gen_cache = new_gen
    _settings_gen_read_at = 0.0          # force a fresh read next access
    _settings_gen_local = new_gen
    logger.info("[llm-chain] rotator reset (settings gen=%d)", new_gen)
    return new_gen


# --------------------------------------------------------------------------- #
# Unified Router — single instance shared across all factories
# --------------------------------------------------------------------------- #
def _get_router() -> Router:
    """Build the unified LiteLLM Router once per process. Shared state lives
    in Redis (cooldown cache + per-deployment TPM/RPM tracking) so all Celery
    workers see the same circuit-breaker state. Rebuilds when the Redis
    settings-generation counter moves so BYOK edits propagate without a
    redeploy."""
    global _router_instance, _settings_gen_local
    gen = _read_settings_gen()
    if _router_instance is not None and gen == _settings_gen_local:
        return _router_instance
    if _router_instance is not None and gen != _settings_gen_local:
        logger.info(
            "[llm-chain] settings gen %d→%d — rebuilding rotator",
            _settings_gen_local, gen,
        )
        _router_instance = None
        _pinned_chain_cache.clear()
        _pinned_to_parent.clear()
    _settings_gen_local = gen
    # `num_retries` is the CASCADE length; set N-1 so a single request can
    # fall through the entire catalog. `retry_policy` per-error caps the
    # cascade. `allowed_fails_policy` is the CIRCUIT BREAKER (independent of
    # retries): after N failures within the window, cool down the deployment
    # for `cooldown_time` so the next request skips it at 0ms.
    CASCADE_DEPTH = 40
    retry_policy = RetryPolicy(
        AuthenticationErrorRetries = CASCADE_DEPTH,
        ContentPolicyViolationErrorRetries = CASCADE_DEPTH,
        RateLimitErrorRetries = CASCADE_DEPTH,
        BadRequestErrorRetries = CASCADE_DEPTH,
        TimeoutErrorRetries = CASCADE_DEPTH,
        InternalServerErrorRetries = CASCADE_DEPTH,
    )
    allowed_fails_policy = AllowedFailsPolicy(
        AuthenticationErrorAllowedFails = 0,
        BadRequestErrorAllowedFails = 1,
        ContentPolicyViolationErrorAllowedFails = 2,
        # 2026-06-15: tightened 1 → 0 after RR scan kimi-k2.6 429 storm
        # observation. A 429 is unambiguous "I'm rate-limited right now";
        # there is no value in burning a second request to confirm. First
        # strike → cooldown. Multi-arm pool absorbs the loss.
        RateLimitErrorAllowedFails = 0,
        TimeoutErrorAllowedFails = 2,
        InternalServerErrorAllowedFails = 2,
    )
    redis_kwargs = {}
    if "REDIS_HOST" in os.environ:
        host = os.environ["REDIS_HOST"].strip()
        if host:
            redis_kwargs["redis_host"] = host
            redis_kwargs["redis_port"] = int(
                os.environ["REDIS_PORT"].strip() if "REDIS_PORT" in os.environ else "6379"
            )
            if "REDIS_PASSWORD" in os.environ:
                pw = os.environ["REDIS_PASSWORD"].strip()
                if pw:
                    redis_kwargs["redis_password"] = pw
    _router_instance = Router(
        # Combined model_list — dd-all / dd-keylm / dd-reduce-label / dd-embed
        # / rr-strong share the cooldown circuit-breaker + Redis state. Chat
        # pools honor the user's BYOK selection via *_current(); infra pools
        # (dd-keylm, dd-embed) are unconditional — embeddings are mandatory.
        model_list=(
            _all_entries_current()
            + _reduce_label_entries_current()
            + _synth_entries_current()
            + _rr_strong_entries_current()
            + _keylm_entries()
            + _embed_entries()
        ),
        routing_strategy = "simple-shuffle",
        enable_pre_call_checks = True,           # fail-fast — skip cooled-down at 0ms
        allowed_fails = 3,
        allowed_fails_policy = allowed_fails_policy,
        # 2026-06-15: bumped 60 → 120. Free-tier provider rate-limit
        # windows are usually 30-60s (NIM, Groq); 60s thaw was racing
        # the actual reset, so a re-picked arm would 429 again on the
        # next try. 120s gives the provider's real-world bucket time to
        # refill before we let the Router probe it again. Pool has
        # plenty of alternates to absorb the 60s extra cooldown.
        cooldown_time = 120,
        retry_policy = retry_policy,
        num_retries = CASCADE_DEPTH,
        set_verbose = False,
        **redis_kwargs,
    )
    # LiteLLM-bundled langfuse integration DISABLED 2026-04-25: reads
    # `langfuse.version.__version__` which doesn't exist on langfuse v3+.
    # See git log for the full removal rationale. Re-enable via a custom
    # LiteLLM logger when cascade visibility becomes critical.
    return _router_instance


# --------------------------------------------------------------------------- #
# Public chain factories — all serve from dd-all unless noted
# --------------------------------------------------------------------------- #
# Temperature is the per-step variation. T=0.7 for Self-Refine exploration
# (Madaan 2023 §2); T=0.0 elsewhere. Per-entry timeouts in the catalog reflect
# provider characteristics; the factory-level timeout args are kept for API
# compatibility only.
def build_llm_fallback_chain(groq_timeout_s: int = 120, nim_timeout_s: int = 300):
    """General-purpose chain. Unified dd-all at T=0.0.

    Returns a `_RotatorAutoRetryRouter` (a ChatLiteLLMRouter subclass) so a
    NIM 404 on any single arm (account-inaccessible model in the dynamic
    catalog) is auto-learned via mark_inaccessible() + retried against the
    rebuilt model_list instead of propagating to the caller. Transparent
    to DeepAgents (passes isinstance(BaseChatModel) since ChatLiteLLMRouter
    inherits from BaseChatModel).
    """
    return _RotatorAutoRetryRouter(
        router = _get_router(), model = GROUP, temperature = 0.0,
    )


def build_rr_strong_chain():
    """Strong-tier chain for the Research Radar DeepAgents orchestrator.

    Returns a `_RotatorAutoRetryRouter` pointed at the curated `rr-strong`
    pool (6 hand-picked Cerebras + NIM + Mistral arms, all proven
    tool-callers on RR smokes). Used in `apps/fastapi/domains/rr/agent/
    graph.py::_orchestrator_model`.

    Why a separate pool: the dd-all simple-shuffle routinely landed RR's
    orchestrator on small models (17B-49B) that couldn't follow the
    6-phase plan, or on reasoning models that emitted XML-style tool
    calls Groq rejects, or on models the bandit hadn't cooled down. The
    rr-strong pool eliminates all 3 failure classes at the routing layer.

    Note: as of Wave 1 (2026-06-16) `build_rr_strong_chain_bandit()` is the
    new RR default — same pool, but each turn picks a deployment via
    FGTS-VA bandit (same routing brain Planner/Synth use) instead of
    LiteLLM Router's simple-shuffle. This factory is kept as the rollback
    target for env `KD_RR_BANDIT_CHAT=false`.
    """
    return _RotatorAutoRetryRouter(
        router = _get_router(), model = RR_STRONG_GROUP, temperature = 0.0,
    )


def build_rr_strong_chain_bandit():
    """Bandit-routed strong-tier chain for RR (Wave 1.2 — 2026-06-16).

    Drop-in for `build_rr_strong_chain` that swaps LiteLLM Router's
    simple-shuffle for FGTS-VA bandit selection per LLM turn. Same pool
    (rr-strong), same tool_calls / response_format passthrough — only the
    deployment-selection brain changes. The new brain matches the one
    Planner (`chat_judge_bandit_async`) and Synth (`pick_synth_deployment_bandit`)
    already use; per-arm rewards/penalties feed the same FGTS-VA cells so
    routing quality improves with every RR scan AND every DD run.

    Falls back to simple-shuffle when Redis is unavailable OR all bandit-
    ranked arms fail, so a Redis blip never kills the agent. Surfaces the
    real deployment id (not the `rr-strong` group alias) via the parent's
    `_create_chat_result` override.

    Gating: `apps/fastapi/domains/rr/agent/graph.py::_subagent_model`
    chooses this factory when `KD_RR_BANDIT_CHAT` is unset OR truthy
    (default ON); `KD_RR_BANDIT_CHAT=false` reverts to
    `build_rr_strong_chain()` for instant rollback.
    """
    return _BanditRoutedRotatorChain(
        router = _get_router(), model = RR_STRONG_GROUP, temperature = 0.0,
    )


def build_resolver_llm_chain(groq_timeout_s: int = 30, nim_timeout_s: int = 60):
    """Resolver chain. Unified dd-all at T=0.0."""
    return ChatLiteLLMRouter(
        router = _get_router(), 
        model = GROUP, 
        temperature = 0.0)


def build_synth_fallback_chain(groq_timeout_s: int = 120, nim_timeout_s: int = 300):
    """Synthesize_chapter + curator chain. DD_USE_SYNTH_POOL=1 routes to the
    dd-synth non-reasoning pool; default uses dd-all."""
    use_synth_pool = (
        "DD_USE_SYNTH_POOL" in os.environ
        and os.environ["DD_USE_SYNTH_POOL"].strip().lower() in ("1", "true", "yes")
    )
    target_group = SYNTH_GROUP if use_synth_pool else GROUP
    return ChatLiteLLMRouter(
        router = _get_router(), 
        model = target_group, 
        temperature = 0.0)


def build_synth_pool_chain():
    """Explicit factory for dd-synth — for harnesses that always want the
    synth pool regardless of env."""
    return ChatLiteLLMRouter(
        router = _get_router(), 
        model = SYNTH_GROUP, 
        temperature = 0.0)


# --------------------------------------------------------------------------- #
# Per-chapter pinning — one deployment for all section synth + refine iters
# --------------------------------------------------------------------------- #
# Pre-pin, refine iterations saw different models per iter — the refiner's
# "you missed hash X, Y, Z" feedback couldn't act on output it didn't generate.
# `seed=chapter.number` → deterministic; same chapter always picks the same
# deployment, even across study runs.
def pick_synth_deployment(seed: int) -> str:
    """Deterministic round-robin over dd-synth. seed=chapter.number. Fallback
    when bandit-driven pinning is disabled or fails."""
    entries = _synth_entries_current()
    if not entries:
        raise RuntimeError("SYNTH_GROUP is empty — cannot pin a deployment")
    return entries[seed % len(entries)]["litellm_params"]["model"]


async def pick_synth_deployment_bandit(
    seed: int,
    *,
    chapter_number: int = 0,
    expected_hash_count: int = 0,
    vault_size: int = 0,
    has_thinking_budget: bool = False,
) -> str:
    """Bandit-driven chapter-pin. Replaces static round-robin with a per-
    chapter ParetoBandit query informed by warm-start benchmark priors +
    accumulated production observations.

    Top-K cascade with atomic provider-slot + deployment-slot reservations.
    On bandit/Redis failure, falls back to pick_synth_deployment(seed)."""
    await ensure_dynamic_catalog()
    entries = _synth_entries_current()
    if not entries:
        raise RuntimeError("SYNTH_GROUP is empty — cannot pin a deployment")
    try:
        if "REDIS_HOST" not in os.environ:
            raise RuntimeError("REDIS_HOST unset")
        host = os.environ["REDIS_HOST"].strip()
        port = os.environ["REDIS_PORT"].strip() if "REDIS_PORT" in os.environ else "6379"
        password = os.environ["REDIS_PASSWORD"].strip() if "REDIS_PASSWORD" in os.environ else ""
        url = f"redis://:{password}@{host}:{port}" if password else f"redis://{host}:{port}"
        rds = redis_aio.from_url(url)
        try:
            candidates = [e["litellm_params"]["model"] for e in entries]
            ctx = bandit.make_context_vector(
                "dd-synth",
                chapter_number = chapter_number,
                expected_hash_count = expected_hash_count,
                vault_size = vault_size,
                has_thinking_budget = has_thinking_budget,
            )
            # K=5: provider-aware reservation needs alternatives when the
            # highest-scoring provider's slots are full.
            ranked = await bandit.predict_top_k(
                "dd-synth", 
                ctx, 
                candidates, 
                redis = rds, 
                k = 5,
            )
            for deployment_id, ucb_score, n_obs in ranked:
                provider = (deployment_id.split("/", 1)[0]
                            if "/" in deployment_id else deployment_id)
                provider_cap = _PROVIDER_CHAPTER_CAPS.get(provider, 2)
                slot = await bandit.try_reserve_provider_slot(
                    provider, 
                    redis = rds, 
                    max_slots = provider_cap, 
                    ttl_s = 1800,
                )
                if slot is None:
                    logger.info(
                        f"[bandit-pin] ch{chapter_number:02d} skipping "
                        f"{deployment_id} (provider {provider!r} full at "
                        f"{provider_cap} chapters); trying next"
                    )
                    continue
                reserved = await bandit.try_reserve(
                    deployment_id, 
                    "dd-synth", 
                    redis = rds, 
                    ttl_s = 1800,
                )
                if not reserved:
                    # Release provider slot — another chapter holds the
                    # deployment lock; we'd be double-booking.
                    await bandit.release_provider_slot(
                        provider, 
                        slot, 
                        redis = rds)
                    logger.info(
                        f"[bandit-pin] ch{chapter_number:02d} skipping "
                        f"{deployment_id} (deployment reserved); trying next"
                    )
                    continue
                logger.info(
                    f"[bandit-pin] ch{chapter_number:02d} → {deployment_id} "
                    f"(ucb={ucb_score:.4f}, n_obs={n_obs}, "
                    f"provider_slot={provider}:{slot})"
                )
                return deployment_id
            logger.warning(
                f"[bandit-pin] ch{chapter_number:02d} all top-{len(ranked)} "
                f"slots saturated; falling through to round-robin"
            )
        finally:
            try:
                await rds.aclose()
            except Exception:
                pass
    except Exception as e:
        logger.warning(
            f"[bandit-pin] ch{chapter_number:02d} bandit pick failed "
            f"({type(e).__name__}: {e}); falling back to round-robin"
        )
    return pick_synth_deployment(seed)


# --------------------------------------------------------------------------- #
# YCS Neo4j entity-extraction pick — separate bandit task key
# --------------------------------------------------------------------------- #
# Phase 3 of the YCS ingest pipeline (LLMGraphTransformer entity+relationship
# extraction over full transcripts). Shares the dd-synth POOL (those models are
# the JSON-strong / structured-output cohort the graph transformer needs) but
# the bandit cells live under `dd_process="ycs-neo4j"` so per-arm σ²_ewma
# evolves on YCS feedback only — DD's mixed-task variance never drags down
# a JSON-strong arm here, and vice-versa.
_YCS_NEO4J_PROCESS = "ycs-neo4j"


# Hard-blocklist of arms that have been observed to reject LangChain's
# `LLMGraphTransformer` `DynamicGraph` Pydantic schema (the structured-
# output `response_format` they emit contains an `anyOf` arm without
# the inner `required` keyword that some providers strict-validate).
# When the bandit picks one of these the run produces 0 nodes — the
# silent-zero guard catches it and emits a `-0.40` penalty, but a) the
# user still loses the run, b) the bandit takes 3-5 cycles to fully
# demote it. Hard-filtering BEFORE `predict_top_k` skips both costs.
#
# Each entry's failure mode is observed and reproducible:
#
#   groq/openai/gpt-oss-120b
#       BadRequestError: 'DynamicGraph': /properties/nodes/anyOf/0/items/
#       required: `required` is required to be supplied
#       (2026-06-08 evening, 2 runs)
#
#   nvidia_nim/stepfun-ai/step-3.5-flash
#       HTTP 200 OK but returns {nodes: [], relationships: []} — the
#       model passes the schema validator but doesn't perform the
#       extraction task (~8 min wall-clock for one empty response)
#       (2026-06-08 evening)
#
#   gemini/gemini-2.5-pro
#       GeminiException BadRequestError on the DynamicGraph schema —
#       Gemini's structured-output enforcement is even stricter than
#       Groq's (rejects on `additionalProperties` too).
#       (2026-06-08 evening)
#
# Override via env `YCS_NEO4J_ARM_ALLOWLIST=…` (comma-separated) if a
# specific blocked arm needs to be re-tested after an upstream fix.
# Static blocklist EMPTIED 2026-06-08 evening after the
# `LLMGraphTransformer(ignore_tool_usage=True)` shipment fixed the
# cross-provider compatibility root cause. The 4 arms previously
# listed here (gpt-oss-120b, step-3.5-flash, gemini-2.5-pro,
# qwen3.5-397b) were all failing because LangChain's default
# `with_structured_output(method="function_calling")` path fights
# each provider's function-calling schema validator. Switching to
# the unstructured plain-text-prompt path makes all of them work.
#
# Helper kept so env override (YCS_NEO4J_ARM_ALLOWLIST is now a
# misnomer in absence of a blocklist; treat it as a HOTFIX channel
# if a NEW model regresses) still functions without code changes.
# The silent-zero guard stays armed as defensive backstop in case a
# specific arm STILL produces 0 nodes for an extraction-amenable
# transcript — bandit will demote it organically.
_YCS_NEO4J_ARM_BLOCKLIST: frozenset[str] = frozenset()


def _ycs_neo4j_filter_candidates(candidates: list[str]) -> list[str]:
    """Drop blocked arms unless the user has explicitly re-allowed
    them via env. Empty result falls back to the unfiltered list so
    the picker can't lock itself out (better to retry a broken arm
    than to 503 the whole pipeline).

    Groq arms are dropped PROVIDER-WIDE for this process: Groq's free
    tier caps the SYNTH-class models at 8 000 tokens/min while one YCS
    Phase 3 request is a FULL transcript (5-10K tokens by design —
    chunking was rejected at -30% entity quality). Observed 2026-06-10
    (groq/openai/gpt-oss-120b): single requests of 8 335 and 9 398
    tokens rejected with deterministic 'Request too large … TPM Limit
    8000' — the arm can never process most real transcripts, it just
    burns a swap segment every time the bandit explores it.
    `YCS_NEO4J_ARM_ALLOWLIST` re-enables specific arms (e.g. after a
    Groq Dev-Tier upgrade)."""
    allow_env = os.environ.get("YCS_NEO4J_ARM_ALLOWLIST", "").strip()
    allow = {m.strip() for m in allow_env.split(",") if m.strip()}
    filtered = [c for c in candidates if c not in _YCS_NEO4J_ARM_BLOCKLIST or c in allow]
    filtered = [c for c in filtered if not c.startswith("groq/") or c in allow]
    if not filtered:
        logger.warning(
            "[ycs-bandit-pin] blocklist would empty the pool — "
            "falling back to unfiltered candidates"
        )
        return candidates
    n_dropped = len(candidates) - len(filtered)
    if n_dropped:
        logger.info(
            f"[ycs-bandit-pin] filtered {n_dropped} unfit arm(s) "
            f"(blocklist + groq TPM floor; {len(filtered)} remain)"
        )
    return filtered


def _pick_synth_deployment_excluding(
    seed: int, exclude: frozenset[str] | set[str],
) -> str:
    """Round-robin over dd-synth, skipping already-tried arms. The plain
    `pick_synth_deployment` ignores `exclude`, so a saturated-pool fallthrough
    re-handed the swap loop an arm it had just circuit-broken (observed
    2026-06-10: kimi-k2.6 re-picked for segment 3 after being excluded in
    segment 2). Falls back to the unfiltered pool only if exclusion empties
    it (better a repeat than a crash)."""
    entries = _synth_entries_current()
    if not entries:
        raise RuntimeError("SYNTH_GROUP is empty — cannot pin a deployment")
    models = [e["litellm_params"]["model"] for e in entries]
    pool = [m for m in models if m not in exclude] or models
    return pool[seed % len(pool)]


async def release_ycs_provider_slot(
    provider: str | None, slot: int | None,
) -> None:
    """Release a provider concurrency slot reserved by
    `pick_ycs_neo4j_deployment_bandit`. The swap loop calls this after each
    segment so a multi-segment run doesn't hold every tried arm's slot for
    the full 1800s TTL — which previously saturated the (DD-shared) provider
    pool mid-run and forced the round-robin fallthrough. No-ops on a fallback
    pick (provider/slot None) or when Redis is absent."""
    if not provider or slot is None or "REDIS_HOST" not in os.environ:
        return
    host = os.environ["REDIS_HOST"].strip()
    port = os.environ["REDIS_PORT"].strip() if "REDIS_PORT" in os.environ else "6379"
    password = os.environ["REDIS_PASSWORD"].strip() if "REDIS_PASSWORD" in os.environ else ""
    url = f"redis://:{password}@{host}:{port}" if password else f"redis://{host}:{port}"
    rds = redis_aio.from_url(url)
    try:
        await bandit.release_provider_slot(provider, slot, redis = rds)
    except Exception as e:
        logger.warning(
            f"[ycs-bandit-pin] provider-slot release failed "
            f"({provider}:{slot}): {type(e).__name__}: {e}"
        )
    finally:
        try:
            await rds.aclose()
        except Exception:
            pass


async def pick_ycs_neo4j_deployment_bandit(
    seed: int,
    *,
    video_count: int = 0,
    exclude: frozenset[str] | set[str] = frozenset(),
) -> tuple[str, str | None, int | None]:
    """Bandit-driven deployment pick for YCS Phase 3 (Neo4j entity extraction).
    One pick per arm-segment of a Celery task. All transcripts in a segment
    share the pinned model. Per-segment reward is recorded by the caller after
    `extract_and_store_graph` returns.

    `exclude` carries the arms already tried (and circuit-broken) within
    THIS run, so a mid-run swap never re-picks the arm that just burned —
    the bandit's demotion only lands after the reward, which is exactly
    when the swap happens. No-empty guard: if exclusion would empty the
    pool, the unfiltered list is kept (better a repeat than a 503).

    Drops back to `pick_synth_deployment(seed)` (deterministic round-robin)
    when the bandit/Redis path errors — but exclude-aware (see
    `_pick_synth_deployment_excluding`) so the fallthrough never re-hands a
    just-failed arm. Returns `(deployment_id, provider, slot)`; provider/slot
    are None on the fallback path. The caller MUST `release_ycs_provider_slot`
    the returned slot when the segment ends (swap or finish) — otherwise the
    reserved slot lingers for its 1800s TTL and saturates the shared pool."""
    await ensure_dynamic_catalog()
    entries = _synth_entries_current()
    if not entries:
        raise RuntimeError("SYNTH_GROUP is empty — cannot pin a YCS deployment")
    try:
        if "REDIS_HOST" not in os.environ:
            raise RuntimeError("REDIS_HOST unset")
        host = os.environ["REDIS_HOST"].strip()
        port = os.environ["REDIS_PORT"].strip() if "REDIS_PORT" in os.environ else "6379"
        password = os.environ["REDIS_PASSWORD"].strip() if "REDIS_PASSWORD" in os.environ else ""
        url = f"redis://:{password}@{host}:{port}" if password else f"redis://{host}:{port}"
        rds = redis_aio.from_url(url)
        try:
            candidates = [e["litellm_params"]["model"] for e in entries]
            # Filter blocklisted arms BEFORE predict_top_k so the bandit
            # never burns a real observation on a known-broken arm.
            candidates = _ycs_neo4j_filter_candidates(candidates)
            if exclude:
                kept = [c for c in candidates if c not in exclude]
                if kept:
                    candidates = kept
                else:
                    logger.warning(
                        "[ycs-bandit-pin] exclusion would empty the pool — "
                        "keeping unfiltered candidates"
                    )
            # `vault_size` is the closest analogue to the YCS workload —
            # the bandit's vault-size buckets (v[4-6]) inform exploration
            # bias for big-input runs, matching how many transcripts will
            # be processed. chapter_number/expected_hash_count stay 0
            # (no DD-style structure here).
            ctx = bandit.make_context_vector(
                _YCS_NEO4J_PROCESS,
                vault_size = video_count,
            )
            ranked = await bandit.predict_top_k(
                _YCS_NEO4J_PROCESS,
                ctx,
                candidates,
                redis = rds,
                k = 5,
            )
            for deployment_id, score, n_obs in ranked:
                provider = (deployment_id.split("/", 1)[0]
                            if "/" in deployment_id else deployment_id)
                provider_cap = _PROVIDER_CHAPTER_CAPS.get(provider, 2)
                slot = await bandit.try_reserve_provider_slot(
                    provider,
                    redis = rds,
                    max_slots = provider_cap,
                    ttl_s = 1800,
                )
                if slot is None:
                    logger.info(
                        f"[ycs-bandit-pin] skipping {deployment_id} (provider "
                        f"{provider!r} full at {provider_cap}); trying next"
                    )
                    continue
                reserved = await bandit.try_reserve(
                    deployment_id,
                    _YCS_NEO4J_PROCESS,
                    redis = rds,
                    ttl_s = 1800,
                )
                if not reserved:
                    await bandit.release_provider_slot(
                        provider, slot, redis = rds,
                    )
                    logger.info(
                        f"[ycs-bandit-pin] skipping {deployment_id} "
                        f"(deployment reserved); trying next"
                    )
                    continue
                logger.info(
                    f"[ycs-bandit-pin] picked {deployment_id} "
                    f"(score={score:.4f}, n_obs={n_obs}, "
                    f"provider_slot={provider}:{slot}, videos={video_count})"
                )
                return deployment_id, provider, slot
            logger.warning(
                f"[ycs-bandit-pin] all top-{len(ranked)} slots saturated; "
                "falling through to round-robin"
            )
        finally:
            try:
                await rds.aclose()
            except Exception:
                pass
    except Exception as e:
        logger.warning(
            f"[ycs-bandit-pin] bandit pick failed ({type(e).__name__}: {e}); "
            "falling back to round-robin"
        )
    return _pick_synth_deployment_excluding(seed, exclude), None, None


async def record_ycs_neo4j_reward(
    deployment_id: str,
    *,
    success: bool,
    latency_s: float | None,
    error_class: str | None = None,
    video_count: int = 0,
    schema_valid: bool = True,
) -> bool:
    """Post-task reward update for the YCS Neo4j bandit cell.

    Called once per Phase 3 Celery task after `extract_and_store_graph`
    returns. Aggregates the run's outcome into a single observation:
      - success = True only if all transcripts processed without exception
      - schema_valid encodes "the LLMGraphTransformer JSON parsed cleanly
        across the run" (default True; flip to False if the caller detects
        a structural break)
      - latency_s = wall-clock of the entire extract pass; the bandit's
        latency expectation is configured per-task (see below).

    Failure dominates: a single SIGTERM/timeout kills the reward regardless
    of how many videos preceded the break. That's intentional — partial
    success doesn't mean the model is reliable for this task.

    Best-effort: Redis/bandit errors are logged + swallowed; the task
    succeeds even when reward emission fails (cold-start scenarios)."""
    try:
        if "REDIS_HOST" not in os.environ:
            return False
        host = os.environ["REDIS_HOST"].strip()
        port = os.environ["REDIS_PORT"].strip() if "REDIS_PORT" in os.environ else "6379"
        password = os.environ["REDIS_PASSWORD"].strip() if "REDIS_PASSWORD" in os.environ else ""
        url = f"redis://:{password}@{host}:{port}" if password else f"redis://{host}:{port}"
        rds = redis_aio.from_url(url)
        try:
            ctx = bandit.make_context_vector(
                _YCS_NEO4J_PROCESS,
                vault_size = video_count,
            )
            # YCS Phase 3 wall-clock expectation: ~4 min per video on
            # full transcripts (LLMGraphTransformer over 14-18K chars).
            expected_latency_s = max(60.0, 240.0 * max(1, video_count))
            reward = bandit.compose_reward(
                success = success,
                schema_valid = schema_valid,
                latency_s = latency_s,
                expected_latency_s = expected_latency_s,
                error_class = error_class,
            )
            ok = await bandit.update(
                deployment_id,
                _YCS_NEO4J_PROCESS,
                ctx,
                reward,
                redis = rds,
            )
            logger.info(
                f"[ycs-bandit-pin] reward update {deployment_id}: "
                f"reward={reward:+.3f} (success={success}, "
                f"latency={latency_s}s, err={error_class})"
            )
            return ok
        finally:
            # Best-effort release of provider+deployment slot reservations.
            try:
                provider = (deployment_id.split("/", 1)[0]
                            if "/" in deployment_id else deployment_id)
                # The slot index isn't tracked across pick/record (we'd
                # need a separate Redis indirection). The 1800s TTL on
                # `try_reserve_provider_slot` lets it self-clear; the
                # deployment reservation we release explicitly below.
                await bandit.release_reservation(
                    deployment_id,
                    _YCS_NEO4J_PROCESS,
                    redis = rds,
                )
            except Exception:
                pass
            try:
                await rds.aclose()
            except Exception:
                pass
    except Exception as e:
        logger.warning(
            f"[ycs-bandit-pin] reward update failed for {deployment_id}: "
            f"{type(e).__name__}: {e}"
        )
        return False


# YCS Phase 3 entity extraction needs a longer per-call ceiling than
# synth's 180s: with `ignore_tool_usage=True` each arm emits the ENTIRE
# entity graph as one plain-text JSON, so an entity-dense transcript
# (e.g. a tax/economics explainer naming a dozen specific taxes + laws)
# generates 10-15K OUTPUT tokens — on a loaded free-tier NIM at ~30-80
# tok/s that crosses 180s and the client deadline kills a still-
# generating call (observed 2026-06-10: 5 arms all cut off at exactly
# 180.0s on one dense Capital Global video). 300s default clears that
# while staying under the 600s batch watchdog (GRAPH_BATCH_TIMEOUT_S).
# Env-tunable; if pushed past ~480 the watchdog must rise too or it
# fires first. Does NOT touch synth's entries (separate cache key).
YCS_NEO4J_EXTRACT_TIMEOUT_S = max(
    60, int(os.environ.get("YCS_NEO4J_EXTRACT_TIMEOUT_S", "300") or "300"),
)


def build_ycs_neo4j_pinned_chain(pinned_model: str):
    """ChatLiteLLMRouter pinned to one YCS Phase 3 deployment. Reuses the
    SYNTH_GROUP catalog entries + LiteLLM Router shape, but with a LONGER
    per-call timeout (`YCS_NEO4J_EXTRACT_TIMEOUT_S`, default 300s vs synth's
    180s) — dense-transcript entity extraction generates a large JSON that
    routinely exceeds 180s of output generation under free-tier load. The
    override participates in the pinned-chain cache key, so the same model
    keeps independent 180s-synth and 300s-YCS chains.

    Falls back to the full synth pool when `pinned_model` isn't in
    SYNTH_GROUP (e.g. user disabled it via /settings between pick and
    chain build)."""
    chain = build_pinned_chain_any(
        pinned_model,
        group = SYNTH_GROUP,
        timeout_override = YCS_NEO4J_EXTRACT_TIMEOUT_S,
    )
    if chain is not None:
        return chain
    logger.warning(
        f"[ycs-pin] {pinned_model!r} not in SYNTH_GROUP; falling back to full pool"
    )
    return build_synth_pool_chain()


# --------------------------------------------------------------------------- #
# Pinned-chain helpers
# --------------------------------------------------------------------------- #
def get_parent_group(pinned_or_parent: str | None) -> str | None:
    """Return the parent pool name (dd-synth / dd-all / dd-reduce-label) for
    a pinned-group hash, or None if it's already a parent or unknown."""
    if not pinned_or_parent:
        return None
    return _pinned_to_parent.get(pinned_or_parent)


def get_entries_for_group(group: str) -> list:
    """Current model entries for a parent pool name. Used by the bandit cascade
    to enumerate candidates when the caller's llm is a pinned (1-entry) chain."""
    if group == SYNTH_GROUP:
        return _synth_entries_current()
    if group == REDUCE_LABEL_GROUP:
        return _reduce_label_entries_current()
    if group == GROUP:
        return _all_entries_current()
    return []


def _build_pinned_chain(pinned_group: str, fresh_entry: dict):
    """Single-deployment Router + ChatLiteLLMRouter with retry discipline
    tuned for a PINNED arm (2026-06-09, after a YCS step-3.5-flash hang
    burned 3-min timeouts in a 12x retry storm — 3 hidden OpenAI-SDK
    attempts inside each of 4 router attempts):

      - TimeoutErrorRetries=0 — a model that just spent the full
        `timeout_s` budget will not get faster on the next attempt; with
        one deployment there is no other arm to rotate to, so a timeout
        retry is guaranteed-futile wall-clock burn. Cross-run arm
        selection (the bandit) is the real retry mechanism.
      - RateLimitErrorRetries=2 — 429s ARE transient here (NIM shares
        one key across DD + YCS; bursts pass in seconds).
      - BadRequestErrorRetries=0 — deterministic schema rejections
        don't fix themselves.
      - num_retries=1 stays as the fallback for error classes the
        policy doesn't enumerate (connection resets etc.).

    The OpenAI SDK's own hidden retries (max_retries=2 hardcoded in
    litellm's client construction; per-call AND litellm_params values
    are stripped by the param mapper — verified in-container) are
    disabled process-wide by the SDK-boundary patch at the top of this
    module, NOT here. Each router attempt is therefore exactly one
    HTTP request."""
    pinned_router = Router(
        model_list = [fresh_entry],
        routing_strategy = "simple-shuffle",
        enable_pre_call_checks = True,
        num_retries = 1,
        retry_policy = RetryPolicy(
            TimeoutErrorRetries                = 0,
            RateLimitErrorRetries              = 2,
            InternalServerErrorRetries         = 1,
            BadRequestErrorRetries             = 0,
            AuthenticationErrorRetries         = 0,
            ContentPolicyViolationErrorRetries = 0,
        ),
        cooldown_time = 30,
        set_verbose = False,
    )
    return ChatLiteLLMRouter(
        router = pinned_router,
        model = pinned_group,
        temperature = 0.0)


def build_pinned_chain_any(
    pinned_model: str,
    group: str | None = None,
    timeout_override: int | None = None,
):
    """Generalized per-call pinning. Build a single-deployment ChatLiteLLMRouter
    for any litellm_params.model string. Searches dd-synth → dd-reduce-label →
    dd-all unless `group` is specified. None when pinned_model isn't found
    (caller falls back).

    `timeout_override` (seconds) replaces the per-deployment `timeout` baked
    into the catalog entry — used by YCS Phase 3, whose dense-transcript
    entity extraction needs a longer ceiling than synth's 180s (see
    `build_ycs_neo4j_pinned_chain`). It participates in the cache key so the
    same model can hold BOTH a 180s synth chain and a 300s YCS chain without
    one clobbering the other."""
    cache_key = (
        pinned_model if timeout_override is None
        else f"{pinned_model}@to{timeout_override}"
    )
    if cache_key in _pinned_chain_cache:
        return _pinned_chain_cache[cache_key]
    search_groups: list[tuple[str, list[dict]]] = []
    if group is None or group == SYNTH_GROUP:
        search_groups.append((SYNTH_GROUP, _synth_entries_current()))
    if group is None or group == REDUCE_LABEL_GROUP:
        search_groups.append((REDUCE_LABEL_GROUP, _reduce_label_entries_current()))
    if group is None or group == GROUP:
        search_groups.append((GROUP, _all_entries_current()))
    matching_entry: dict | None = None
    matched_group: str | None = None
    for grp_name, entries in search_groups:
        for e in entries:
            if e["litellm_params"]["model"] == pinned_model:
                matching_entry = e
                matched_group = grp_name
                break
        if matching_entry is not None:
            break
    if matching_entry is None:
        return None
    pinned_group = f"dd-pinned-{abs(hash(cache_key)) & 0xFFFFFF:06x}"
    litellm_params = dict(matching_entry["litellm_params"])
    if timeout_override is not None:
        litellm_params["timeout"] = timeout_override
    fresh_entry = {
        "model_name":    pinned_group,
        "litellm_params": litellm_params,
    }
    chain = _build_pinned_chain(pinned_group, fresh_entry)
    _pinned_chain_cache[cache_key] = chain
    _pinned_to_parent[pinned_group] = matched_group or GROUP
    return chain


def build_synth_pinned_chain(pinned_model: str):
    """Single-deployment ChatLiteLLMRouter targeting `pinned_model` from
    dd-synth. Falls back to the full pool if `pinned_model` isn't in dd-synth
    (e.g. someone disabled it mid-run)."""
    if pinned_model in _pinned_chain_cache:
        return _pinned_chain_cache[pinned_model]
    matching = [
        e for e in _synth_entries_current()
        if e["litellm_params"]["model"] == pinned_model
    ]
    if not matching:
        logger.warning(f"[synth-pin] {pinned_model!r} not in SYNTH_GROUP; falling back to full pool")
        return build_synth_pool_chain()
    pinned_group = f"dd-synth-pinned-{abs(hash(pinned_model)) & 0xFFFFFF:06x}"
    fresh_entry = {
        "model_name":    pinned_group,
        "litellm_params": dict(matching[0]["litellm_params"]),
    }
    chain = _build_pinned_chain(pinned_group, fresh_entry)
    _pinned_chain_cache[pinned_model] = chain
    _pinned_to_parent[pinned_group] = SYNTH_GROUP
    return chain


def build_refine_llm_chain(groq_timeout_s: int = 120, nim_timeout_s: int = 300):
    """Self-Refine refiner at T=0.7 (Madaan 2023 §2). Unified dd-all."""
    return ChatLiteLLMRouter(
        router=_get_router(), 
        model = GROUP, 
        temperature = 0.7)


def build_curator_llm(timeout_s: int = 600):
    """Curator chain. Unified dd-all at T=0.0."""
    return ChatLiteLLMRouter(
        router = _get_router(), 
        model = GROUP, 
        temperature = 0.0)


def build_keylm_chain():
    """Tiny-LM chain for KeyLLM cluster labels. Routes to KEYLM_GROUP — NIM
    Llama-3.2-1B primary, 3B fallback. T=0.0; max_tokens applied per call."""
    return ChatLiteLLMRouter(
        router = _get_router(), 
        model = KEYLM_GROUP, 
        temperature = 0.0)


def build_reduce_label_chain():
    """Non-reasoning rotator for REDUCE step's labeling + ordering. T=1.0 —
    Gemini-3 requires it (T<1.0 causes infinite loops). The factory's call-
    time T overrides per-deployment litellm_params.temperature in LiteLLM
    Router. json_schema mode keeps output valid at T=1.0 for non-Gemini.

    2026-06-17 — returns `_RotatorAutoRetryRouter` instead of plain
    `ChatLiteLLMRouter` so the real deployment id (e.g.
    `groq/llama-3.3-70b-versatile`) is written into every chunk's
    `response_metadata["model_name"]` via the override in
    `_RotatorAutoRetryRouter._create_chat_result`. Without it,
    consumers like YCS Query's `_ModelCapture` only saw the request
    group alias (`dd-reduce-label`) and the UI's "Model" chip
    rendered it verbatim. Auto-retry / empty-generation recovery /
    NotFoundError auto-learn come for free."""
    return _RotatorAutoRetryRouter(
        router = _get_router(),
        model = REDUCE_LABEL_GROUP,
        temperature = 1.0,
    )


# --------------------------------------------------------------------------- #
# Dynamic catalog — discovery + benchmarks → top-K per step
# --------------------------------------------------------------------------- #
def _record_to_entry(group: str, record, timeout_s: int) -> dict | None:
    """Convert a discovery.DiscoveryRecord → LiteLLM entry dict. Dispatches by
    provider so the resulting shape is byte-identical to the static catalog.
    None for unsupported providers (SambaNova/DeepSeek-direct held disabled)."""
    p, m = record.provider, record.model_id
    if not m:
        return None
    if p == "groq":     return _groq_entry(group, m,     timeout_s = timeout_s)
    if p == "nim":      return _nim_entry(group, m,      timeout_s = timeout_s)
    if p == "cerebras": return _cerebras_entry(group, m, timeout_s = timeout_s)
    if p == "mistral":  return _mistral_entry(group, m,  timeout_s = timeout_s)
    if p == "gemini":   return _gemini_entry(group, m,   timeout_s = timeout_s)
    return None


# All three apply selection_filter so BYOK is honored EVERYWHERE — Router
# model_list AND FGTS-VA bandit candidate pools (which bypass Router via
# litellm.acompletion). Single source of truth so the bandit never picks a
# deselected model. No-empty guard inside the filter keeps each pool alive.
def _all_entries_current() -> list:
    """Active dd-all catalog — dynamic if available, else static fallback;
    trimmed to enabled∩selected, then to account-accessible."""
    if _dynamic_catalog_initialized and _dynamic_entries.get("dd-all"):
        return _apply_inaccessibility_filter(
            _apply_selection_filter(_dynamic_entries["dd-all"])
        )
    return _apply_inaccessibility_filter(
        _apply_selection_filter(_all_entries())
    )


def _synth_entries_current() -> list:
    """Active dd-synth catalog — dynamic if available, else static fallback;
    trimmed to enabled∩selected, then to account-accessible."""
    if _dynamic_catalog_initialized and _dynamic_entries.get("dd-synth"):
        return _apply_inaccessibility_filter(
            _apply_selection_filter(_dynamic_entries["dd-synth"])
        )
    return _apply_inaccessibility_filter(
        _apply_selection_filter(_synth_entries())
    )


def _reduce_label_entries_current() -> list:
    """Active dd-reduce-label catalog — dynamic if available, else static;
    trimmed to enabled∩selected, then to account-accessible."""
    if _dynamic_catalog_initialized and _dynamic_entries.get("dd-reduce-label"):
        return _apply_inaccessibility_filter(
            _apply_selection_filter(_dynamic_entries["dd-reduce-label"])
        )
    return _apply_inaccessibility_filter(
        _apply_selection_filter(_reduce_label_entries())
    )


def _rr_strong_entries_current() -> list:
    """Active rr-strong catalog. Static only — the curated 6-arm pool is
    intentionally NOT discovered live (we WANT determinism here so the
    orchestrator never gets routed to an unproven model). Inaccessibility
    + selection filters still apply so a user-disabled provider drops
    out cleanly."""
    return _apply_inaccessibility_filter(
        _apply_selection_filter(_rr_strong_entries())
    )


def _build_redis_url_for_bench() -> str | None:
    """Construct Redis URL from env for benchmark cache. None when REDIS_HOST unset."""
    if "REDIS_HOST" not in os.environ:
        return None
    host = os.environ["REDIS_HOST"].strip()
    if not host:
        return None
    port = os.environ["REDIS_PORT"].strip() if "REDIS_PORT" in os.environ else "6379"
    password = os.environ["REDIS_PASSWORD"].strip() if "REDIS_PASSWORD" in os.environ else ""
    return f"redis://:{password}@{host}:{port}" if password else f"redis://{host}:{port}"


async def init_dynamic_catalog(force: bool = False) -> bool:
    """Build _dynamic_entries from live discovery + benchmark ranking, filtered
    by BYOK selection.

      - provider in 'all' mode    → all discovered free models eligible, ranked,
        capped at per-step top-K.
      - provider in 'custom' mode → ONLY selected model_ids, ALWAYS kept (never
        dropped by top-K).
      - no selection                → top-K benchmark slice of every discovered model.

    Idempotent unless force=True. Returns True when dynamic is active. Any
    failure → static fallback (still selection-filtered by *_current())."""
    global _dynamic_catalog_initialized, _dynamic_built_gen
    if "DD_DYNAMIC_CATALOG" in os.environ:
        if os.environ["DD_DYNAMIC_CATALOG"].strip().lower() not in ("1", "true", "yes", "on"):
            logger.info("[llm-chain] DD_DYNAMIC_CATALOG=0 — using static catalog")
            return False
    if _dynamic_catalog_initialized and not force:
        return True
    gen_at_build = _read_settings_gen()
    # Stamp the attempted gen up front. A failed build (no keys → discovery
    # returns 0) does NOT retry discovery on every bandit call —
    # ensure_dynamic_catalog only rebuilds when the gen MOVES (user changed
    # /settings, which bumps it), so adding a key kicks a fresh attempt.
    _dynamic_built_gen = gen_at_build
    sel = _read_selection(force=True)
    min_param_b = _DYNAMIC_MIN_PARAM_B
    if "KD_DYNAMIC_MIN_PARAM_B" in os.environ:
        try:
            min_param_b = float(os.environ["KD_DYNAMIC_MIN_PARAM_B"])
        except (TypeError, ValueError):
            pass
    try:
        redis_url = _build_redis_url_for_bench()
        rds = redis_aio.from_url(redis_url) if redis_url else None
        new_entries: dict[str, list[dict]] = {}
        try:
            by_provider = await discovery.list_all_alive_models()
            alive = [r for records in by_provider.values() for r in records]
            if not alive:
                raise RuntimeError("discovery returned 0 alive models")
            # Apply BYOK selection up front. No-empty guard: empty selection
            # falls back to all discovered.
            if sel:
                alive_sel = [r for r in alive if selection_allows(r.provider, r.model_id, sel)]
                if not alive_sel:
                    logger.warning(
                        "[llm-chain] dynamic catalog: selection emptied the "
                        "discovered pool — using all discovered (no-empty guard)"
                    )
                    alive_sel = alive
            else:
                alive_sel = alive
            logger.info(
                f"[llm-chain] dynamic catalog: {len(alive)} discovered, "
                f"{len(alive_sel)} after selection across "
                f"{len(by_provider)} providers"
            )
            for step, step_cfg in DYNAMIC_STEPS.items():
                try:
                    ranked = await benchmarks.rank_for_step(step, alive_sel, redis=rds)
                except Exception as e:
                    logger.warning(
                        f"[llm-chain] rank_for_step({step}) failed: "
                        f"{type(e).__name__}: {e}; using static for this step"
                    )
                    continue
                # Custom-mode records = explicit choices → ALWAYS kept (never
                # cut by top-K, never floored). All-mode records fill the
                # remaining top-K budget (scored first, unscored backfill).
                custom_recs: list = []
                scored_all: list = []
                unscored_all: list = []
                for record, score in ranked:
                    if provider_mode(record.provider, sel) == "custom":
                        custom_recs.append(record)
                    elif score > 0:
                        scored_all.append(record)
                    else:
                        unscored_all.append(record)
                # Two gates on the auto-fill (custom picks exempt):
                # 1. non-chat exclusion on EVERY chat pool (embedders/rerankers).
                # 2. capability size floor on HEAVY pools only (dd-all/dd-synth).
                fill = scored_all + unscored_all
                orig_fill = fill
                n_nonchat = sum(1 for r in fill if is_non_chat_model(r.model_id))
                fill = [r for r in fill if not is_non_chat_model(r.model_id)]
                n_size = 0
                if step in _DYNAMIC_QUALITY_FLOOR_STEPS and min_param_b > 0:
                    floored = [r for r in fill if passes_capability_floor(r.model_id, min_param_b)]
                    n_size = len(fill) - len(floored)
                    fill = floored
                # No-empty guard.
                if not fill and not custom_recs:
                    logger.warning(
                        f"[llm-chain] dynamic catalog: {step} filters emptied "
                        f"the pool — keeping unfiltered (no-empty guard)"
                    )
                    fill = orig_fill
                else:
                    if n_nonchat:
                        logger.info(
                            f"[llm-chain] dynamic catalog: {step} dropped "
                            f"{n_nonchat} non-chat model(s)"
                        )
                    if n_size:
                        logger.info(
                            f"[llm-chain] dynamic catalog: {step} quality floor "
                            f"(>={min_param_b:g}B or MoE) dropped {n_size} small model(s)"
                        )
                budget = max(0, step_cfg.top_k - len(custom_recs))
                pool_records = custom_recs + fill[:budget]
                entries: list[dict] = []
                seen: set[str] = set()
                for r in pool_records:
                    entry = _record_to_entry(step_cfg.group, r, step_cfg.timeout_s)
                    if entry is None:
                        continue
                    key = entry["litellm_params"]["model"]
                    if key in seen:
                        continue
                    seen.add(key)
                    entries.append(entry)
                if entries:
                    new_entries[step] = entries
                    logger.info(
                        f"[llm-chain] dynamic catalog: {step} → {len(entries)} "
                        f"entries ({len(custom_recs)} custom-pinned, cap "
                        f"top-K={step_cfg.top_k})"
                    )
                else:
                    logger.warning(
                        f"[llm-chain] dynamic catalog: {step} produced 0 entries; "
                        f"static fallback for this step"
                    )
        finally:
            if rds:
                try:
                    await rds.aclose()
                except Exception:
                    pass
        # Atomic swap — populate a temp dict then replace in place. No await
        # between clear+update so concurrent readers never see a half-built map.
        if new_entries:
            _dynamic_entries.clear()
            _dynamic_entries.update(new_entries)
            _dynamic_catalog_initialized = True
            _dynamic_built_gen = gen_at_build
            logger.info(
                f"[llm-chain] dynamic catalog ACTIVE for: "
                f"{sorted(_dynamic_entries.keys())} (gen={gen_at_build})"
            )
            return True
        logger.warning("[llm-chain] dynamic catalog: 0 steps populated; full static fallback")
        _dynamic_entries.clear()
        _dynamic_catalog_initialized = False
        _dynamic_built_gen = gen_at_build
        return False
    except Exception as e:
        logger.warning(
            f"[llm-chain] dynamic catalog init failed: "
            f"{type(e).__name__}: {e}; using static catalog"
        )
        _dynamic_entries.clear()
        _dynamic_catalog_initialized = False
        return False


async def ensure_dynamic_catalog() -> None:
    """Lazy (re)build of the dynamic catalog on the async hot path when the
    BYOK settings generation has moved (selection changed in any process) or
    it was never built. Cheap when fresh — one throttled Redis gen read.
    This is what propagates a /settings change to every Celery worker without
    a redeploy."""
    if "DD_DYNAMIC_CATALOG" in os.environ:
        if os.environ["DD_DYNAMIC_CATALOG"].strip().lower() not in ("1", "true", "yes", "on"):
            return
    # Rebuild only when the settings generation MOVES. A failed attempt
    # already stamped this gen so we don't hammer discovery while keyless —
    # adding a key bumps the gen and kicks a rebuild.
    if _read_settings_gen() != _dynamic_built_gen:
        await init_dynamic_catalog(force = True)


def init_dynamic_catalog_sync() -> bool:
    """Sync wrapper for non-async callers (Celery worker_process_init). Spins
    up a private event loop — do NOT call from inside an existing loop."""
    try:
        return asyncio.run(init_dynamic_catalog())
    except Exception as e:
        logger.warning(
            f"[llm-chain] init_dynamic_catalog_sync failed: {type(e).__name__}: {e}"
        )
        return False


# --------------------------------------------------------------------------- #
# Periodic catalog refresh — EOL resilience for every provider in the registry
# --------------------------------------------------------------------------- #
# The litellm gen-config.sh app (`~/.config/litellm/gen-config.sh`) re-fetches
# NIM's /v1/models on every service (re)start so the catalog tracks NIM's live
# model list automatically. We do the same — but recurring, and across EVERY
# provider — so EOL events (`z-ai/glm5` cycled out 2026-05-18, NIM nemotron
# function IDs decommissioned) don't wedge running workers until next
# redeploy. Combined with the EOL-broadened `_RotatorAutoRetryRouter`, the
# rotator now:
#
#   1. Catches EOL/410/deprecated/404 errors at call time and adds the
#      offending model to `_RUNTIME_INACCESSIBLE_MODELS` (immediate fallover).
#   2. Periodically re-runs discovery so the dynamic catalog drops models
#      that providers have cycled out of /v1/models entirely.
#
# Interval default 900s (15 min) — fast enough to catch same-day EOLs, slow
# enough to be invisible to provider rate limits. Override via env
# `DD_CATALOG_REFRESH_INTERVAL_S` (set 0 to disable).
_CATALOG_REFRESH_INTERVAL_S: int = 900

_catalog_refresh_task: asyncio.Task | None = None


def _catalog_refresh_interval() -> int:
    interval = _CATALOG_REFRESH_INTERVAL_S
    if "DD_CATALOG_REFRESH_INTERVAL_S" in os.environ:
        try:
            interval = int(os.environ["DD_CATALOG_REFRESH_INTERVAL_S"])
        except (TypeError, ValueError):
            pass
    return interval


async def _catalog_refresh_loop() -> None:
    """Background loop: every `interval` seconds, force a fresh discovery
    fan-out + dynamic-catalog rebuild. Errors are logged + swallowed so a
    transient provider outage doesn't kill the loop."""
    interval = _catalog_refresh_interval()
    if interval <= 0:
        logger.info("[llm-chain] catalog refresh loop disabled (interval<=0)")
        return
    logger.info(
        f"[llm-chain] catalog refresh loop started (every {interval}s)"
    )
    while True:
        try:
            await asyncio.sleep(interval)
            ok = await init_dynamic_catalog(force = True)
            n_runtime = len(_RUNTIME_INACCESSIBLE_MODELS)
            logger.info(
                f"[llm-chain] periodic refresh complete: "
                f"dynamic_active={ok}, runtime_blocklist={n_runtime}"
            )
        except asyncio.CancelledError:
            logger.info("[llm-chain] catalog refresh loop cancelled")
            return
        except Exception as e:
            logger.warning(
                f"[llm-chain] catalog refresh loop iteration failed: "
                f"{type(e).__name__}: {e}"
            )


def start_catalog_refresh_loop() -> asyncio.Task | None:
    """Spawn the periodic refresh task on the running event loop. Idempotent
    — second call returns the existing task. Returns `None` if there is no
    running loop (caller is expected to be inside FastAPI lifespan)."""
    global _catalog_refresh_task
    if _catalog_refresh_task is not None and not _catalog_refresh_task.done():
        return _catalog_refresh_task
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning(
            "[llm-chain] start_catalog_refresh_loop called outside event loop"
        )
        return None
    _catalog_refresh_task = loop.create_task(
        _catalog_refresh_loop(),
        name = "llm-rotator-catalog-refresh",
    )
    return _catalog_refresh_task


async def stop_catalog_refresh_loop() -> None:
    """Cancel the refresh task on FastAPI shutdown. Awaits the cancellation
    so the loop's `finally` blocks (if any) run before the event loop
    closes."""
    global _catalog_refresh_task
    if _catalog_refresh_task is None:
        return
    _catalog_refresh_task.cancel()
    try:
        await _catalog_refresh_task
    except (asyncio.CancelledError, Exception):
        pass
    _catalog_refresh_task = None
