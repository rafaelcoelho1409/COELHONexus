"""Unified LLM Router — LiteLLM-backed with fail-fast pre-call checks.

SECURITY: litellm pinned to 1.83.12 (v1.82.7/1.82.8 compromised 2026-03-24).
Do NOT allow litellm>=1.82.7,<1.83.0.

Dynamic catalog (DD_DYNAMIC_CATALOG=1 default): discovery + benchmarks → top-K
per step. Static fallback on failure. BYOK filter applied via `*_current()`.
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
import redis as _redis_sync
import redis.asyncio as redis_aio
from litellm import Router
from litellm.types.router import AllowedFailsPolicy, RetryPolicy
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langchain_core.messages.utils import convert_to_openai_messages
from langchain_core.outputs import ChatGenerationChunk
from langchain_litellm.chat_models import ChatLiteLLMRouter
from langchain_litellm.chat_models.litellm_router import (
    _convert_delta_to_message_chunk,
    _create_usage_metadata,
)


# modify_params: strips thinking_blocks from assistant messages on cascade (prevents IndexError at chat_models.py:508).
# drop_params: removes unsupported per-call params (e.g. nemotron-4-340b rejecting `tools`).
litellm.modify_params = True
litellm.drop_params   = True


from domains.llm.credentials import get_store, resolve_key
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


# Patch openai SDK max_retries=0 process-wide. litellm 1.83.13 hardcodes
# `data.pop("max_retries", 2)` and strips per-call `max_retries` in the param
# mapper, so every Router attempt was silently 3 SDK retries × per-deployment
# timeout. Owned-at-SDK-boundary is the only knob that reaches this path.
try:
    import openai as _openai_sdk

    if not getattr(_openai_sdk, "_dd_no_sdk_retries", False):
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
        _openai_sdk._dd_no_sdk_retries = True
        logger.info("[llm-chain] OpenAI SDK hidden retries disabled (max_retries=0)")
except Exception as _sdk_patch_err:
    logger.warning(
        f"[llm-chain] failed to disable OpenAI SDK retries "
        f"({type(_sdk_patch_err).__name__}: {_sdk_patch_err})"
    )


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


# Per-loop cap on in-flight bandit-routed LLM calls. WeakKeyDict so the
# semaphore is GC'd with the loop (each Celery task gets its own asyncio.run).
import weakref as _weakref
_RR_LLM_SEM_BY_LOOP: _weakref.WeakKeyDictionary = _weakref.WeakKeyDictionary()


def _get_rr_llm_sem() -> asyncio.Semaphore:
    """Per-loop semaphore capping concurrent bandit-routed RR LLM calls.
    KD_RR_SEM env overrides (default 8)."""
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


# Per-provider in-flight caps; bandit skips at-cap providers (bypasses Router, so routing_strategy can't help).
_RR_PROVIDER_CAPS: dict[str, int] = {
    "nvidia_nim": 4,  # 40 RPM ÷ ~20s
    "groq":       2,  # 30 RPM peak; tighter cap absorbs bursts
    "cerebras":   2,
    "mistral":    3,
    "gemini":     2,
    "deepseek":   2,
    "sambanova":  2,  # 20 RPM — tightest cap
}

_RR_PROVIDER_INFLIGHT_BY_LOOP: _weakref.WeakKeyDictionary = _weakref.WeakKeyDictionary()


def _get_rr_provider_inflight() -> dict[str, int]:
    """Per-loop dict tracking in-flight RR LLM calls by provider."""
    loop = asyncio.get_running_loop()
    state = _RR_PROVIDER_INFLIGHT_BY_LOOP.get(loop)
    if state is None:
        state = {}
        _RR_PROVIDER_INFLIGHT_BY_LOOP[loop] = state
    return state


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


def _keylm_entries() -> list:
    """Two-deep small-LM rotator for KeyLLM cluster labeling. NIM-only;
    3B is fallback after NIM 40 RPM saturated on 28-cluster bursts."""
    return [
        _nim_entry(KEYLM_GROUP, "meta/llama-3.2-1b-instruct", timeout_s = 30),
        _nim_entry(KEYLM_GROUP, "meta/llama-3.2-3b-instruct", timeout_s = 45),
    ]


def _reduce_label_entries() -> list:
    """Non-reasoning rotator for REDUCE labeling/ordering. Fastest LPU/TPU first."""
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
    Reasoning first (structured-output completeness gates post-synth audit);
    non-reasoning Tier 2/3 absorb cooldown."""
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
    """Single-entry embedding group — rotating providers breaks cosine
    geometry within a study. NIM requires encoding_format."""
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


def _require_nim_key() -> str:
    """Resolved NVIDIA NIM key or raise — NIM is mandatory for embed+rerank."""
    key = resolve_key("NVIDIA_API_KEY")
    if not key:
        raise RuntimeError(_NIM_REQUIRED_MSG)
    return key


def embed_via_router_sync(
    texts: list[str],
    input_type: str = "passage",
) -> list[list[float]]:
    """Sync batch-embed via dd-embed. input_type: "passage" for indexed docs,
    "query" for short anchor strings — asymmetric models lose 3-8 cosine pts
    on the wrong head."""
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
    """Async embed. on_batch is fire-and-forget — callback errors swallowed."""
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
    """Single-shot dd-all Router judge. Prefer chat_judge_bandit_async for
    repeated calls — lets the bandit learn arm reliability."""
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
        _bump_dd_llm_counter(response, deployment=GROUP)
    return (response.choices[0].message.content or "").strip()


def _bump_dd_llm_counter(response, deployment: str | None = None) -> dict | None:
    """Best-effort DD node-level LLM accounting.

    The DD counter module no-ops unless a Planner/Synth node wrapper has
    set attribution context, so this is safe for RR/YCS/settings callers.
    """
    try:
        from domains.dd.runtime.llm_counter import bump_current_call
        return bump_current_call(response=response, deployment=deployment)
    except Exception as e:
        logger.warning(
            f"[dd-llm-counter] rotator bump failed: "
            f"{type(e).__name__}: {e}"
        )
        return None


async def _redis_for_bandit():
    """Lazy Redis client for ParetoBandit. None on env-misconfig."""
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
    """Drop expired per-arm 429 cooldown entries."""
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
    """Bandit-routed single-shot judge cascading top-K via direct
    litellm.acompletion (bypasses Router shuffle). Submits reward per attempt.
    Falls back to Router-shuffle on infra failure.

    dd_process: bandit-cell namespace; default "dd-grader". Pass "dd-synth-write"
    for SAWC writer drafts. candidate_filter excludes BEFORE predict_top_k.
    response_format attaches ONLY to _RESPONSE_FORMAT_SAFE_PROVIDERS.
    """
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
        # Empty-filter → keep full set rather than 503.
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
        # Drop cooling arms; empty → keep original (better 429 than 503).
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
                        # response_format only for providers that translate cleanly.
                        if response_format is not None and any(
                            deployment_id.startswith(p) for p in _RESPONSE_FORMAT_SAFE_PROVIDERS
                        ):
                            acompletion_kwargs["response_format"] = response_format
                        response = await litellm.acompletion(**acompletion_kwargs)
                        attempt_span.attach_chat_response(response)
                        dd_counter = _bump_dd_llm_counter(
                            response,
                            deployment=deployment_id,
                        )
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
                        # 429 → cooldown across all dd_process cascades in this window.
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
                        "usage":       dd_counter,
                    }
                # Success+bad-schema: return; cascade can't fix a schema quirk.
                if success:
                    return response_text, {
                        "deployment":     deployment_id,
                        "attempts":       attempts,
                        "latency_s":      latency_s,
                        "reward":         reward,
                        "schema_invalid": True,
                        "dd_process":     effective_process,
                        "usage":          dd_counter,
                    }
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
    """Cross-encoder rerank via NIM hosted API. Returns (orig_index, logit) desc.
    NIM logits are raw (~[-12, +12]); caller thresholds. Direct httpx — NIM
    rerank API isn't OpenAI-compat."""
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


def _rr_strong_entries() -> list:
    """Curated 9-arm strong-tier pool for the RR orchestrator. All 120B+
    frontier tool-callers proven on dd-synth. Order only affects predict_top_k
    tie-break (lowest n_obs first). Smaller arms (17B-49B) excluded due to
    phantom-completion mode. SambaNova + Cerebras excluded — their "free"
    tier requires payment method / returns 404."""
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
    """Static dd-all fallback. Strict benchmark order."""
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


def _read_selection(force: bool = False) -> dict:
    """The user's BYOK selection blob. force=True bypasses the store's TTL cache."""
    try:
        return get_store().read_settings(force = force) or {}
    except Exception:
        return {}


def _apply_selection_filter(entries: list[dict]) -> list[dict]:
    """Trim entries to the user's enabled providers + selected models.
    No-empty guard: empty result → keep entries unchanged."""
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


# Hard-blocklist of NIM models that 404 with "Function not found for account"
# on every call. LiteLLM 1.83's RetryPolicy lacks NotFoundErrorRetries so a
# Router pick on these arms terminates the cascade. Retire when LiteLLM > 1.85.
_ACCOUNT_INACCESSIBLE_BLOCKLIST: frozenset[str] = frozenset({
    "nvidia/llama-3.1-nemotron-ultra-253b-v1",
    "nvidia/nemotron-4-340b-instruct",
})


# Runtime-learned 404s — augments the static blocklist for this process lifetime.
_RUNTIME_INACCESSIBLE_MODELS: set[str] = set()


def mark_inaccessible(model_id: str) -> None:
    """Add a model to the runtime blocklist + reset the Router. Idempotent."""
    if not model_id or model_id in _RUNTIME_INACCESSIBLE_MODELS:
        return
    _RUNTIME_INACCESSIBLE_MODELS.add(model_id)
    logger.warning(
        f"[llm-chain] runtime auto-blocklist: {model_id!r} (NIM 404 observed)"
    )
    # bump_gen=False — per-process learning, not a cluster-wide settings change.
    reset_rotator(bump_gen=False)


def _apply_inaccessibility_filter(entries: list[dict]) -> list[dict]:
    """Drop arms in static + runtime inaccessibility blocklists. Substring
    match on `litellm_params.model`. No-empty guard."""
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


# Auto-retry Router subclass — catches `litellm.NotFoundError`, parses the
# failing model out of error text, marks it inaccessible, refreshes Router,
# retries. Subclassing (not wrapping) ChatLiteLLMRouter because DeepAgents'
# `resolve_model` does `isinstance(BaseChatModel)`; a wrapper would fail.
import re as _re
_MODEL_RE = _re.compile(r"litellm\.acompletion\(model=([^)\s]+)\)")
_NIM_PREFIX_RE = _re.compile(r"nvidia_nim/([\w./-]+)")
# Cerebras + others use "Model <name> does not exist" prose form.
_DOES_NOT_EXIST_RE = _re.compile(
    r"Model\s+([\w./-]+)\s+does\s+not\s+exist",
    _re.IGNORECASE,
)


_GROUP_NAMES: frozenset[str] = frozenset({
    "dd-all", "rr-strong", "dd-synth", "dd-reduce-label",
    "dd-keylm", "dd-embed",
})


def _extract_model_from_error(err_text: str, exc: BaseException | None = None) -> str | None:
    """Pull failing model id from a litellm exception. Returns bare model id
    so it round-trips through _apply_inaccessibility_filter's substring match.
    Tries exception attrs (rejecting group aliases), then log-line, prose,
    bare-prefix matches."""
    if exc is not None:
        for attr in ("model", "llm_provider"):
            val = getattr(exc, attr, None)
            if isinstance(val, str) and val and val not in _GROUP_NAMES:
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


_MAX_NOTFOUND_RETRIES = 6


def _flatten_thinking_content(messages):
    """Flatten list-shaped content on AI/Tool/HumanMessage so providers
    declaring `content: str` (Cerebras/Mistral/Groq) don't reject list-form
    thinking blocks. Without this, cascade exhausts → IndexError at
    langchain_core/chat_models.py:508. LiteLLM's modify_params handles
    orphaned tool_calls but does NOT strip list-content."""
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
                # thinking/reasoning/redacted_thinking/image/tool_use blocks dropped.
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
            # tool_call_id is REQUIRED to link the tool result back to AIMessage.
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
            # Unknown subclass — pass through unchanged.
            out.append(m)
    return out


class _RotatorAutoRetryRouter(ChatLiteLLMRouter):
    """ChatLiteLLMRouter subclass with three cross-provider survival fixes:
    (1) flatten thinking-block list-content so non-list-tolerant providers
    don't 400; (2) catch NotFoundError → mark_inaccessible → retry; (3)
    surface real deployment id (e.g. `nvidia_nim/openai/gpt-oss-120b`) in
    `response_metadata["model_name"]` instead of the group alias."""

    def _create_chat_result(self, response, **params):
        """Augment parent's ChatResult with the real deployment id from
        response["model"]."""
        result = super()._create_chat_result(response, **params)
        try:
            real_model = None
            if isinstance(response, dict):
                real_model = response.get("model")
            else:
                real_model = getattr(response, "model", None)
            if isinstance(real_model, str) and real_model:
                if isinstance(result.llm_output, dict):
                    result.llm_output["model_name"] = real_model
                for gen in result.generations or []:
                    msg = getattr(gen, "message", None)
                    if msg is None:
                        continue
                    rm = getattr(msg, "response_metadata", None)
                    if isinstance(rm, dict):
                        rm["model_name"] = real_model
                    else:
                        try:
                            msg.response_metadata = {"model_name": real_model}
                        except Exception:
                            pass
        except Exception:
            pass
        return result

    def _request_model_name(self) -> str:
        return str(
            getattr(self, "model_name", None)
            or getattr(self, "model", None)
            or GROUP
        )

    def _messages_for_observability(self, messages) -> list[dict]:
        try:
            payload = convert_to_openai_messages(messages)
            if isinstance(payload, list):
                return payload
        except Exception:
            pass
        return [{"role": "user", "content": str(messages)}]

    def _coerce_text_content(self, value) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            parts: list[str] = []
            for block in value:
                if isinstance(block, dict):
                    text = block.get("text") or block.get("content") or ""
                    if text:
                        parts.append(str(text))
                elif block:
                    parts.append(str(block))
            return "\n".join(parts).strip()
        if value is None:
            return ""
        return str(value)

    def _temperature_for_call(self, kwargs: dict) -> float | None:
        val = kwargs.get("temperature", getattr(self, "temperature", None))
        return float(val) if val is not None else None

    def _attach_chat_result_to_span(self, span: Any, result: Any) -> None:
        generations = getattr(result, "generations", None) or []
        first = generations[0] if generations else None
        if isinstance(first, list):
            first = first[0] if first else None
        message = getattr(first, "message", None)
        response_metadata = dict(getattr(message, "response_metadata", None) or {})
        usage_metadata = getattr(message, "usage_metadata", None) or {}
        if hasattr(usage_metadata, "model_dump"):
            try:
                usage_metadata = usage_metadata.model_dump()
            except Exception:
                usage_metadata = {}
        elif not isinstance(usage_metadata, dict):
            usage_metadata = dict(getattr(usage_metadata, "__dict__", {}) or {})
        llm_output = getattr(result, "llm_output", None) or {}
        usage: dict[str, int] = {}
        input_tokens = (
            usage_metadata.get("input_tokens")
            or usage_metadata.get("prompt_tokens")
        )
        output_tokens = (
            usage_metadata.get("output_tokens")
            or usage_metadata.get("completion_tokens")
        )
        if input_tokens is not None:
            usage["prompt_tokens"] = int(input_tokens)
        if output_tokens is not None:
            usage["completion_tokens"] = int(output_tokens)
        finish_reason = response_metadata.get("finish_reason")
        content = self._coerce_text_content(getattr(message, "content", "") or "")
        response_payload: dict[str, Any] = {
            "model": (
                response_metadata.get("model_name")
                or llm_output.get("model_name")
                or self._request_model_name()
            ),
            "usage": usage,
            "choices": [{
                "message": {"content": content},
            }],
        }
        if response_metadata.get("id"):
            response_payload["id"] = response_metadata["id"]
        if finish_reason:
            response_payload["choices"][0]["finish_reason"] = finish_reason
        span.attach_chat_response(response_payload)

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        messages = _flatten_thinking_content(messages)
        should_stream = kwargs.get("stream")
        if should_stream is None:
            should_stream = bool(getattr(self, "streaming", False))
        if should_stream:
            return await super()._agenerate(
                messages, stop=stop, run_manager=run_manager, **kwargs,
            )
        request_messages = self._messages_for_observability(messages)
        last_err: Exception | None = None
        async with genai_completion_span(
            request_model = self._request_model_name(),
            messages      = request_messages,
            temperature   = self._temperature_for_call(kwargs),
            max_tokens    = kwargs.get("max_tokens"),
            top_p         = kwargs.get("top_p"),
        ) as completion_span:
            for attempt in range(_MAX_NOTFOUND_RETRIES):
                try:
                    result = await super()._agenerate(
                        messages, stop=stop, run_manager=run_manager, **kwargs,
                    )
                except Exception as e:
                    # is_eol_error covers NotFoundError(404) + 410/deprecated paths.
                    if not (isinstance(e, litellm.NotFoundError) or is_eol_error(e)):
                        raise
                    last_err = e
                    model = _extract_model_from_error(str(e), exc=e)
                    if model:
                        mark_inaccessible(model)
                    else:
                        # Unidentified deployment (NIM hides model behind a UUID) —
                        # force a reshuffle; Router's allowed_fails will demote it.
                        logger.warning(
                            f"[rotator-retry] EOL-class error on attempt "
                            f"{attempt+1} from unidentified deployment; forcing "
                            f"Router reshuffle"
                        )
                    self.router = _get_router()
                    continue
                # Empty generations (Gemini policy filter, parse glitch, {"choices":[]})
                # would crash at langchain_core/chat_models.py:508 — treat as soft fail.
                if not result.generations or not result.generations[0]:
                    logger.warning(
                        f"[rotator-retry] empty generations on attempt {attempt+1}; "
                        f"forcing deployment reshuffle"
                    )
                    last_err = RuntimeError("empty generations from rotator")
                    self.router = _get_router()
                    continue
                self._attach_chat_result_to_span(completion_span, result)
                return result
        raise last_err if last_err else RuntimeError(
            "[rotator-retry] exhausted without specific error"
        )

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        messages = _flatten_thinking_content(messages)
        request_messages = self._messages_for_observability(messages)
        async with genai_completion_span(
            request_model = self._request_model_name(),
            messages      = request_messages,
            temperature   = self._temperature_for_call(kwargs),
            max_tokens    = kwargs.get("max_tokens"),
            top_p         = kwargs.get("top_p"),
        ) as completion_span:
            default_chunk_class = AIMessageChunk
            message_dicts, params = self._create_message_dicts(messages, stop)
            params = {**params, **kwargs, "stream": True}
            params = {k: v for k, v in params.items() if v is not None}
            params["stream_options"] = (
                self.stream_options
                if self.stream_options is not None
                else {"include_usage": True}
            )
            self._prepare_params_for_router(params)
            first_chunk_yielded = False
            final_model = self._request_model_name()
            final_usage: dict[str, Any] = {}
            final_id: str | None = None
            finish_reason: str | None = None
            accumulated = ""

            async for raw_chunk in await self.router.acompletion(
                messages=message_dicts, **params
            ):
                usage_metadata = None
                usage = raw_chunk.get("usage") if isinstance(raw_chunk, dict) else None
                if usage:
                    final_usage = usage
                    usage_metadata = _create_usage_metadata(usage)
                if isinstance(raw_chunk, dict):
                    raw_model = raw_chunk.get("model")
                    if raw_model:
                        final_model = str(raw_model)
                    raw_id = raw_chunk.get("id")
                    if raw_id:
                        final_id = str(raw_id)

                choices = raw_chunk.get("choices") if isinstance(raw_chunk, dict) else None
                if not choices:
                    if usage_metadata:
                        chunk_obj = default_chunk_class(
                            content="", usage_metadata=usage_metadata,
                        )
                        cg_chunk = ChatGenerationChunk(message=chunk_obj)
                        if run_manager:
                            await run_manager.on_llm_new_token(
                                "", chunk=cg_chunk, **params,
                            )
                        yield cg_chunk
                    continue

                choice0 = choices[0] or {}
                finish_reason = choice0.get("finish_reason") or finish_reason
                delta = choice0.get("delta") or {}
                chunk = _convert_delta_to_message_chunk(delta, default_chunk_class)

                if usage_metadata and isinstance(chunk, AIMessageChunk):
                    chunk.usage_metadata = usage_metadata

                if isinstance(chunk, AIMessageChunk):
                    chunk.response_metadata = {
                        "model_name": final_model,
                        **({"id": final_id} if final_id else {}),
                        **({"finish_reason": finish_reason} if finish_reason else {}),
                    }
                    accumulated += self._coerce_text_content(chunk.content)
                    first_chunk_yielded = True

                default_chunk_class = chunk.__class__
                cg_chunk = ChatGenerationChunk(message=chunk)
                if run_manager:
                    await run_manager.on_llm_new_token(
                        chunk.content, chunk=cg_chunk, **params,
                    )
                yield cg_chunk

            completion_span.attach_chat_response({
                "model": final_model,
                "id": final_id,
                "usage": final_usage,
                "choices": [{
                    "message": {"content": accumulated},
                    **({"finish_reason": finish_reason} if finish_reason else {}),
                }],
            })

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        messages = _flatten_thinking_content(messages)
        last_err: Exception | None = None
        for attempt in range(_MAX_NOTFOUND_RETRIES):
            try:
                result = super()._generate(
                    messages, stop=stop, run_manager=run_manager, **kwargs,
                )
            except Exception as e:
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
    """RR-only: replaces simple-shuffle with FGTS-VA per LLM turn. Mirrors
    chat_judge_bandit_async's cascade but preserves tool_calls so DeepAgents
    subagent loops work unchanged. Falls back to parent simple-shuffle when
    bandit infra is unavailable or all ranked arms fail. Gated by
    KD_RR_BANDIT_CHAT at factory time."""

    # Separate cell from dd-* so RR rewards/penalties don't leak into DD scoring.
    _RR_DD_PROCESS = RR_STRONG_GROUP

    _RR_EXPECTED_LATENCY_S: float = 30.0

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        messages = _flatten_thinking_content(messages)
        _prune_arm_cooldown()

        async with _get_rr_llm_sem():
            return await self._agenerate_inner(
                messages, stop=stop, run_manager=run_manager, **kwargs,
            )

    async def _agenerate_inner(
        self, messages, stop=None, run_manager=None, **kwargs,
    ):
        """Bandit cascade body — split out so `_agenerate` wraps with the
        per-loop concurrency semaphore."""
        rds = await _redis_for_bandit()
        if rds is None:
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

            # Cooled-down arms — 429 budget shared with chat_judge_bandit.
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

            # bind_tools injects tools/tool_choice via kwargs.
            tools          = kwargs.get("tools")
            tool_choice    = kwargs.get("tool_choice")
            temperature    = kwargs.get("temperature", getattr(self, "temperature", 0.0))
            max_tokens     = kwargs.get("max_tokens")
            response_format = kwargs.get("response_format")
            timeout_by_id  = {
                e["litellm_params"]["model"]: e["litellm_params"].get("timeout", 120)
                for e in entries
            }

            last_err: Exception | None = None
            attempts = 0
            inflight = _get_rr_provider_inflight()
            async with genai_bandit_cascade_span(
                dd_process = self._RR_DD_PROCESS,
            ) as cascade:
                try:
                    for deployment_id, _score, _n_obs in ranked:
                        provider = (
                            deployment_id.split("/", 1)[0] if "/" in deployment_id else ""
                        )
                        # Per-provider cap: skip at-cap arms; cascade advances naturally.
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
                        success = False
                        error_class: str | None = None
                        try:
                            async with genai_bandit_attempt_span(
                                deployment_id = deployment_id,
                                attempt       = attempts,
                                dd_process    = self._RR_DD_PROCESS,
                                messages      = oai_messages,
                                temperature   = temperature,
                                max_tokens    = max_tokens,
                            ) as attempt_span:
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
                                    # response_format only for providers that translate cleanly.
                                    if response_format is not None and any(
                                        deployment_id.startswith(p)
                                        for p in _RESPONSE_FORMAT_SAFE_PROVIDERS
                                    ):
                                        acompletion_kwargs["response_format"] = response_format
                                        # NIM: attach nvext.guided_json so XGrammar enforces
                                        # schema at decode time. KD_RR_GUIDED_JSON gates.
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
                                                pass
                                    response = await litellm.acompletion(**acompletion_kwargs)
                                    attempt_span.attach_chat_response(response)
                                    latency_s = float(time.monotonic() - t0)

                                    # langchain-litellm's `_create_chat_result` does
                                    # `params["metadata"]` → KeyError if absent. Router's
                                    # `_prepare_params_for_router` injects that on the normal
                                    # path; bypassing Router we provide an empty dict ourselves.
                                    result = self._create_chat_result(response, metadata={})

                                    if not result.generations or not result.generations[0]:
                                        last_err = RuntimeError(
                                            f"empty generations from {deployment_id} "
                                            f"(latency_s={latency_s:.2f})"
                                        )
                                        reward = 0.0
                                        update_bandit_outcome(
                                            attempt_span,
                                            latency_s    = latency_s,
                                            reward       = reward,
                                            error_class  = "empty_generations",
                                            schema_valid = False,
                                        )
                                        try:
                                            await bandit.update(
                                                deployment_id, self._RR_DD_PROCESS,
                                                ctx, reward, redis = rds,
                                            )
                                        except Exception:
                                            pass
                                        logger.warning(
                                            f"[rr-bandit] {deployment_id} empty generations; "
                                            f"cascading"
                                        )
                                        continue

                                    success = True
                                    reward = bandit.compose_reward(
                                        success            = True,
                                        schema_valid       = True,
                                        latency_s          = latency_s,
                                        expected_latency_s = self._RR_EXPECTED_LATENCY_S,
                                        error_class        = None,
                                    )
                                    update_bandit_outcome(
                                        attempt_span,
                                        latency_s    = latency_s,
                                        reward       = reward,
                                        error_class  = None,
                                        schema_valid = True,
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
                                    update_bandit_outcome(
                                        attempt_span,
                                        latency_s    = latency_s,
                                        reward       = reward,
                                        error_class  = error_class,
                                        schema_valid = False,
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
                            # Release provider slot on success/fail/skip.
                            inflight[provider] = max(
                                0, inflight.get(provider, 0) - 1
                            )

                    logger.warning(
                        f"[rr-bandit] all {attempts} ranked arms failed (last: "
                        f"{type(last_err).__name__ if last_err else 'None'}); "
                        f"falling back to simple-shuffle Router"
                    )
                    return await super()._agenerate(
                        messages, stop=stop, run_manager=run_manager, **kwargs,
                    )
                finally:
                    cascade.set_total_attempts(attempts)

        finally:
            try:
                await rds.aclose()
            except Exception:
                pass

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        """Sync path defers to parent simple-shuffle. Bandit selection is
        async-only (the actual hot path)."""
        return super()._generate(
            messages, stop=stop, run_manager=run_manager, **kwargs,
        )


def _redis_sync_conn():
    """Sync Redis client for the settings-gen counter."""
    if "REDIS_HOST" not in os.environ:
        return None
    host = os.environ["REDIS_HOST"].strip()
    if not host:
        return None
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
        # Redis blip → keep last known gen; never block hot path.
        pass
    finally:
        try:
            r.close()
        except Exception:
            pass
    return _settings_gen_cache


def reset_rotator(*, bump_gen: bool = True) -> int:
    """Drop Router + pinned caches; INCR Redis gen so other processes rebuild
    on their next access. Returns the new gen."""
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
    _settings_gen_read_at = 0.0  # force fresh read next access
    _settings_gen_local = new_gen
    logger.info("[llm-chain] rotator reset (settings gen=%d)", new_gen)
    return new_gen


def _get_router() -> Router:
    """Build the unified LiteLLM Router once per process. Shared state in
    Redis (cooldown cache + per-deployment TPM/RPM tracking) so all Celery
    workers see the same circuit-breaker state. Rebuilds when settings-gen
    counter moves so BYOK edits propagate without a redeploy."""
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
    # num_retries = cascade length; retry_policy caps per-error;
    # allowed_fails_policy is the CIRCUIT BREAKER (separate from retries).
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
        # First-strike cooldown: 429 is unambiguous, multi-arm pool absorbs.
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
        # Combined model_list — all groups share the cooldown circuit-breaker
        # + Redis state. Chat pools honor BYOK via *_current(); infra pools
        # (dd-keylm, dd-embed) are unconditional.
        model_list=(
            _all_entries_current()
            + _reduce_label_entries_current()
            + _synth_entries_current()
            + _rr_strong_entries_current()
            + _keylm_entries()
            + _embed_entries()
        ),
        routing_strategy = "simple-shuffle",
        enable_pre_call_checks = True,
        allowed_fails = 3,
        allowed_fails_policy = allowed_fails_policy,
        # 120s > free-tier rate-limit windows (30-60s) — avoids race where a
        # re-picked arm 429s on retry while pool absorbs the extra cooldown.
        cooldown_time = 120,
        retry_policy = retry_policy,
        num_retries = CASCADE_DEPTH,
        set_verbose = False,
        **redis_kwargs,
    )
    # LiteLLM-bundled langfuse disabled: reads langfuse.version.__version__ which doesn't exist in v3+.
    return _router_instance


# Factories default to dd-all at T=0.0. T=0.7 for Self-Refine (Madaan 2023 §2).
def build_llm_fallback_chain(groq_timeout_s: int = 120, nim_timeout_s: int = 300):
    """General-purpose dd-all chain at T=0.0. Auto-retry on NIM 404 via
    mark_inaccessible. DeepAgents-compatible (BaseChatModel subclass)."""
    return _RotatorAutoRetryRouter(
        router = _get_router(), model = GROUP, temperature = 0.0,
    )


def build_rr_strong_chain():
    """Strong-tier RR-orchestrator chain. Rollback target for KD_RR_BANDIT_CHAT=false."""
    return _RotatorAutoRetryRouter(
        router = _get_router(), model = RR_STRONG_GROUP, temperature = 0.0,
    )


def build_rr_strong_chain_bandit():
    """Bandit-routed strong-tier chain (default RR path). FGTS-VA per turn
    instead of simple-shuffle; tool_calls/response_format passthrough preserved.
    Falls back to simple-shuffle on Redis unavailable or arm-cascade exhausted."""
    return _BanditRoutedRotatorChain(
        router = _get_router(), model = RR_STRONG_GROUP, temperature = 0.0,
    )


def build_resolver_llm_chain(groq_timeout_s: int = 30, nim_timeout_s: int = 60):
    """Resolver chain — dd-all at T=0.0."""
    return ChatLiteLLMRouter(
        router = _get_router(),
        model = GROUP,
        temperature = 0.0)


def build_synth_fallback_chain(groq_timeout_s: int = 120, nim_timeout_s: int = 300):
    """Synth + curator chain. DD_USE_SYNTH_POOL=1 routes to dd-synth pool."""
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
    """Explicit dd-synth factory regardless of env."""
    return ChatLiteLLMRouter(
        router = _get_router(),
        model = SYNTH_GROUP,
        temperature = 0.0)


# Per-chapter pin. Without pinning, refine iters saw different models per iter,
# so "you missed hash X" feedback couldn't act on output it didn't generate.
def pick_synth_deployment(seed: int) -> str:
    """Deterministic round-robin over dd-synth (seed=chapter.number).
    Fallback when bandit pinning fails."""
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
    """Bandit-driven chapter-pin. Top-K cascade with atomic provider-slot +
    deployment-slot reservations. Falls back to round-robin on Redis failure."""
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
            # K=5 — alternatives for provider-aware reservation cascade.
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
                    # Release provider slot — another chapter holds the lock.
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


# YCS Phase 3 (LLMGraphTransformer entity extraction) shares the dd-synth POOL
# but lives under a separate bandit dd_process so σ²_ewma evolves on YCS
# feedback only — DD's mixed-task variance doesn't drag a JSON-strong arm down.
_YCS_NEO4J_PROCESS = "ycs-neo4j"


# Empty since LLMGraphTransformer(ignore_tool_usage=True) fixed cross-provider
# DynamicGraph schema rejections. Helper kept for hotfix via YCS_NEO4J_ARM_ALLOWLIST.
_YCS_NEO4J_ARM_BLOCKLIST: frozenset[str] = frozenset()


def _ycs_neo4j_filter_candidates(candidates: list[str]) -> list[str]:
    """Drop blocked arms + drop Groq provider-wide (8K TPM floor < single
    YCS transcript's 5-10K tokens; chunking rejected at -30% entity quality).
    YCS_NEO4J_ARM_ALLOWLIST env re-enables specific arms."""
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
    """Round-robin over dd-synth skipping already-tried arms. Without this,
    a saturated-pool fallthrough re-handed the swap loop an arm it had just
    circuit-broken. Empty exclusion → unfiltered pool (better repeat than crash)."""
    entries = _synth_entries_current()
    if not entries:
        raise RuntimeError("SYNTH_GROUP is empty — cannot pin a deployment")
    models = [e["litellm_params"]["model"] for e in entries]
    pool = [m for m in models if m not in exclude] or models
    return pool[seed % len(pool)]


async def release_ycs_provider_slot(
    provider: str | None, slot: int | None,
) -> None:
    """Release a slot reserved by pick_ycs_neo4j_deployment_bandit. Swap
    loop calls per-segment so a multi-segment run doesn't hold every tried
    arm's slot for the full 1800s TTL."""
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
    """Bandit pick for YCS Phase 3. One pick per arm-segment; all transcripts
    in a segment share the pinned model. `exclude` blocks within-run arm reuse
    (the bandit's demotion only lands after the reward, which is when the swap
    happens). Returns (deployment_id, provider, slot); caller MUST call
    `release_ycs_provider_slot` to avoid 1800s-TTL pool saturation."""
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
            # vault_size analogue → bandit's vault-size buckets (v[4-6]).
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
    """Post-task reward update for YCS Neo4j bandit. Failure dominates —
    a single SIGTERM/timeout kills the reward regardless of how many videos
    preceded. Best-effort: Redis errors logged + swallowed."""
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
            # ~4 min/video baseline (LLMGraphTransformer over 14-18K chars).
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
                # Slot index isn't tracked pick→record; 1800s TTL self-clears.
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


# Longer than synth's 180s — ignore_tool_usage=True emits the whole graph as
# one JSON; entity-dense transcripts hit 10-15K output tokens. 300s default
# stays under GRAPH_BATCH_TIMEOUT_S (600s) watchdog.
YCS_NEO4J_EXTRACT_TIMEOUT_S = max(
    60, int(os.environ.get("YCS_NEO4J_EXTRACT_TIMEOUT_S", "300") or "300"),
)


def build_ycs_neo4j_pinned_chain(pinned_model: str):
    """Pinned chain for YCS Phase 3 with extended timeout. Same model keeps
    independent 180s-synth and 300s-YCS chains (timeout in cache key).
    Falls back to full synth pool when pinned_model isn't in SYNTH_GROUP."""
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


def get_parent_group(pinned_or_parent: str | None) -> str | None:
    """Parent pool name for a pinned-group hash, or None."""
    if not pinned_or_parent:
        return None
    return _pinned_to_parent.get(pinned_or_parent)


def get_entries_for_group(group: str) -> list:
    """Current entries for a parent pool — bandit cascade uses this when the
    caller's llm is a pinned (1-entry) chain."""
    if group == SYNTH_GROUP:
        return _synth_entries_current()
    if group == REDUCE_LABEL_GROUP:
        return _reduce_label_entries_current()
    if group == GROUP:
        return _all_entries_current()
    return []


def _build_pinned_chain(pinned_group: str, fresh_entry: dict):
    """Single-deployment Router with PIN-tuned retry discipline:
    TimeoutErrorRetries=0 (no other arm to rotate to → guaranteed-futile);
    RateLimitErrorRetries=2 (NIM shares key across DD+YCS, bursts pass);
    BadRequestErrorRetries=0 (schema rejections are deterministic);
    num_retries=1 for unenumerated classes. OpenAI SDK retries are killed
    process-wide by the top-of-module patch, not here."""
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
    """Generalized pinning. Searches dd-synth → dd-reduce-label → dd-all
    unless `group` specified. None when not found. timeout_override
    participates in the cache key so the same model can hold both 180s-synth
    and 300s-YCS chains."""
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
    """Single-deployment chain pinned to dd-synth `pinned_model`. Falls back
    to full pool if not present (e.g. disabled mid-run)."""
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
    """Self-Refine refiner at T=0.7 (Madaan 2023 §2). dd-all."""
    return ChatLiteLLMRouter(
        router=_get_router(),
        model = GROUP,
        temperature = 0.7)


def build_curator_llm(timeout_s: int = 600):
    """Curator chain — dd-all at T=0.0."""
    return ChatLiteLLMRouter(
        router = _get_router(), 
        model = GROUP, 
        temperature = 0.0)


def build_keylm_chain():
    """Tiny-LM chain for KeyLLM cluster labels — NIM Llama-3.2-1B primary, 3B fallback."""
    return ChatLiteLLMRouter(
        router = _get_router(),
        model = KEYLM_GROUP,
        temperature = 0.0)


def build_reduce_label_chain():
    """Non-reasoning chain for REDUCE labeling/ordering. T=1.0 — Gemini-3
    infinite-loops at T<1.0. json_schema mode keeps non-Gemini output valid.
    Uses _RotatorAutoRetryRouter so real deployment id surfaces in
    response_metadata (UI Model chip)."""
    return _RotatorAutoRetryRouter(
        router = _get_router(),
        model = REDUCE_LABEL_GROUP,
        temperature = 1.0,
    )


def _record_to_entry(group: str, record, timeout_s: int) -> dict | None:
    """DiscoveryRecord → LiteLLM entry. None for unsupported providers."""
    p, m = record.provider, record.model_id
    if not m:
        return None
    if p == "groq":     return _groq_entry(group, m,     timeout_s = timeout_s)
    if p == "nim":      return _nim_entry(group, m,      timeout_s = timeout_s)
    if p == "cerebras": return _cerebras_entry(group, m, timeout_s = timeout_s)
    if p == "mistral":  return _mistral_entry(group, m,  timeout_s = timeout_s)
    if p == "gemini":   return _gemini_entry(group, m,   timeout_s = timeout_s)
    return None


# Single source of truth — Router model_list AND FGTS-VA bandit candidate
# pools (the bandit bypasses Router via litellm.acompletion).
def _all_entries_current() -> list:
    """dd-all — dynamic if available else static; trimmed by selection + inaccessibility."""
    if _dynamic_catalog_initialized and _dynamic_entries.get("dd-all"):
        return _apply_inaccessibility_filter(
            _apply_selection_filter(_dynamic_entries["dd-all"])
        )
    return _apply_inaccessibility_filter(
        _apply_selection_filter(_all_entries())
    )


def _synth_entries_current() -> list:
    """dd-synth — dynamic if available else static; trimmed by selection + inaccessibility."""
    if _dynamic_catalog_initialized and _dynamic_entries.get("dd-synth"):
        return _apply_inaccessibility_filter(
            _apply_selection_filter(_dynamic_entries["dd-synth"])
        )
    return _apply_inaccessibility_filter(
        _apply_selection_filter(_synth_entries())
    )


def _reduce_label_entries_current() -> list:
    """dd-reduce-label — dynamic if available else static; trimmed."""
    if _dynamic_catalog_initialized and _dynamic_entries.get("dd-reduce-label"):
        return _apply_inaccessibility_filter(
            _apply_selection_filter(_dynamic_entries["dd-reduce-label"])
        )
    return _apply_inaccessibility_filter(
        _apply_selection_filter(_reduce_label_entries())
    )


def _rr_strong_entries_current() -> list:
    """rr-strong — static only (curated pool deliberately not discovered live)
    + selection/inaccessibility filters."""
    return _apply_inaccessibility_filter(
        _apply_selection_filter(_rr_strong_entries())
    )


def _build_redis_url_for_bench() -> str | None:
    """Redis URL for benchmark cache; None if REDIS_HOST unset."""
    if "REDIS_HOST" not in os.environ:
        return None
    host = os.environ["REDIS_HOST"].strip()
    if not host:
        return None
    port = os.environ["REDIS_PORT"].strip() if "REDIS_PORT" in os.environ else "6379"
    password = os.environ["REDIS_PASSWORD"].strip() if "REDIS_PASSWORD" in os.environ else ""
    return f"redis://:{password}@{host}:{port}" if password else f"redis://{host}:{port}"


async def init_dynamic_catalog(force: bool = False) -> bool:
    """Build _dynamic_entries from discovery + benchmark ranking, BYOK-filtered.
    'all'-mode providers feed top-K; 'custom'-mode model_ids ALWAYS kept (never
    cut). Idempotent unless force=True. Failure → static fallback."""
    global _dynamic_catalog_initialized, _dynamic_built_gen
    if "DD_DYNAMIC_CATALOG" in os.environ:
        if os.environ["DD_DYNAMIC_CATALOG"].strip().lower() not in ("1", "true", "yes", "on"):
            logger.info("[llm-chain] DD_DYNAMIC_CATALOG=0 — using static catalog")
            return False
    if _dynamic_catalog_initialized and not force:
        return True
    gen_at_build = _read_settings_gen()
    # Stamp attempted gen up front so a failed build (no keys) doesn't retry
    # discovery on every bandit call — ensure_dynamic_catalog only rebuilds
    # when gen moves (adding a key bumps it).
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
            # BYOK selection up front; no-empty guard.
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
                # Custom-mode records ALWAYS kept; all-mode fills remaining top-K.
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
                # Gates on auto-fill (custom exempt): non-chat exclusion
                # everywhere; capability floor on dd-all/dd-synth only.
                fill = scored_all + unscored_all
                orig_fill = fill
                n_nonchat = sum(1 for r in fill if is_non_chat_model(r.model_id))
                fill = [r for r in fill if not is_non_chat_model(r.model_id)]
                n_size = 0
                if step in _DYNAMIC_QUALITY_FLOOR_STEPS and min_param_b > 0:
                    floored = [r for r in fill if passes_capability_floor(r.model_id, min_param_b)]
                    n_size = len(fill) - len(floored)
                    fill = floored
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
        # Atomic swap — no await between clear+update so concurrent readers
        # never see a half-built map.
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
    """Lazy (re)build on hot path when settings-gen moved. Throttled Redis
    gen read; this propagates /settings changes cluster-wide without a redeploy."""
    if "DD_DYNAMIC_CATALOG" in os.environ:
        if os.environ["DD_DYNAMIC_CATALOG"].strip().lower() not in ("1", "true", "yes", "on"):
            return
    # Rebuild only when gen MOVES — failed attempts stamped this gen so we
    # don't hammer discovery while keyless.
    if _read_settings_gen() != _dynamic_built_gen:
        await init_dynamic_catalog(force = True)


def init_dynamic_catalog_sync() -> bool:
    """Sync wrapper (Celery worker_process_init). Do NOT call inside an
    existing loop — spins up its own."""
    try:
        return asyncio.run(init_dynamic_catalog())
    except Exception as e:
        logger.warning(
            f"[llm-chain] init_dynamic_catalog_sync failed: {type(e).__name__}: {e}"
        )
        return False


# Periodic discovery refresh for EOL resilience. Combined with the EOL-broadened
# _RotatorAutoRetryRouter: call-time catches mark inaccessible; periodic
# refreshes drop cycled-out models from /v1/models. Default 900s; env
# DD_CATALOG_REFRESH_INTERVAL_S overrides (0 disables).
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
    """Background loop: every interval, force discovery + catalog rebuild.
    Errors logged + swallowed so a transient outage doesn't kill the loop."""
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
    """Idempotent refresh-task spawn. None if no running loop (caller must be
    inside FastAPI lifespan)."""
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
    """Cancel the refresh task on FastAPI shutdown; awaits so loop's finally
    blocks run before loop closes."""
    global _catalog_refresh_task
    if _catalog_refresh_task is None:
        return
    _catalog_refresh_task.cancel()
    try:
        await _catalog_refresh_task
    except (asyncio.CancelledError, Exception):
        pass
    _catalog_refresh_task = None
