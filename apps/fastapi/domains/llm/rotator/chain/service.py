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

from domains.llm.credentials import resolve_key
from domains.llm.rotator import bandit, benchmarks, discovery

from .config import DYNAMIC_STEPS, JUDGE
from .domain import (
    classify_error,
    entry_provider_and_model,
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


logger = logging.getLogger(__name__)


# When OTEL_EXPORTER_OTLP_ENDPOINT is set, LiteLLM emits a span per LLM call
# (model, deployment_id, provider, token counts, latency, error type) through
# the TracerProvider configured in core.otel. dd_process metadata propagates
# via config={"metadata": {"dd_process": "..."}} on ainvoke.
if "OTEL_EXPORTER_OTLP_ENDPOINT" in os.environ:
    try:
        litellm.callbacks = ["otel"]
        logger.info("[llm-chain] LiteLLM OTel callback enabled")
    except Exception as _ote:
        logger.warning(
            f"[llm-chain] failed to enable LiteLLM OTel callback "
            f"({type(_ote).__name__}: {_ote})"
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
        response = router.embedding(
            model = DD_EMBED_GROUP,
            input = batch,
            encoding_format = "float",
            input_type = input_type,
            truncate = "END",
        )
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
        response = await router.aembedding(
            model = DD_EMBED_GROUP,
            input = batch,
            encoding_format = "float",
            input_type = input_type,
            truncate = "END",
        )
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
    response = await router.acompletion(
        model = GROUP,
        messages = [{"role": "user", "content": prompt}],
        temperature = temperature,
        max_tokens = max_tokens,
    )
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
            try:
                acompletion_kwargs = dict(
                    model = deployment_id,
                    api_key = api_key,
                    messages = [{"role": "user", "content": prompt}],
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
    async with httpx.AsyncClient(timeout = 60.0) as client:
        resp = await client.post(url, json = payload, headers = headers)
        resp.raise_for_status()
        data = resp.json()
    rankings = data.get("rankings") or []
    pairs = [(int(r["index"]), float(r["logit"])) for r in rankings]
    if top_n is not None:
        pairs = pairs[:top_n]
    return pairs


# --------------------------------------------------------------------------- #
# Static catalog assembly (dd-all)
# --------------------------------------------------------------------------- #
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
        RateLimitErrorAllowedFails = 1,
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
        # share the cooldown circuit-breaker + Redis state. Chat pools honor
        # the user's BYOK selection via *_current(); infra pools (dd-keylm,
        # dd-embed) are unconditional — embeddings are mandatory.
        model_list=(
            _all_entries_current()
            + _reduce_label_entries_current()
            + _synth_entries_current()
            + _keylm_entries()
            + _embed_entries()
        ),
        routing_strategy = "simple-shuffle",
        enable_pre_call_checks = True,           # fail-fast — skip cooled-down at 0ms
        allowed_fails = 3,
        allowed_fails_policy = allowed_fails_policy,
        cooldown_time = 60,
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
    """General-purpose chain. Unified dd-all at T=0.0."""
    return ChatLiteLLMRouter(router = _get_router(), model = GROUP, temperature = 0.0)


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


def build_pinned_chain_any(pinned_model: str, group: str | None = None):
    """Generalized per-call pinning. Build a single-deployment ChatLiteLLMRouter
    for any litellm_params.model string. Searches dd-synth → dd-reduce-label →
    dd-all unless `group` is specified. None when pinned_model isn't found
    (caller falls back)."""
    if pinned_model in _pinned_chain_cache:
        return _pinned_chain_cache[pinned_model]
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
    pinned_group = f"dd-pinned-{abs(hash(pinned_model)) & 0xFFFFFF:06x}"
    fresh_entry = {
        "model_name":    pinned_group,
        "litellm_params": dict(matching_entry["litellm_params"]),
    }
    pinned_router = Router(
        model_list = [fresh_entry],
        routing_strategy = "simple-shuffle",
        enable_pre_call_checks = True,
        num_retries = 3,
        cooldown_time = 30,
        set_verbose = False,
    )
    chain = ChatLiteLLMRouter(
        router = pinned_router, 
        model = pinned_group, 
        temperature = 0.0)
    _pinned_chain_cache[pinned_model] = chain
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
    pinned_router = Router(
        model_list = [fresh_entry],
        routing_strategy = "simple-shuffle",
        enable_pre_call_checks = True,
        num_retries = 3,
        cooldown_time = 30,
        set_verbose = False,
    )
    chain = ChatLiteLLMRouter(
        router = pinned_router, 
        model = pinned_group, 
        temperature = 0.0)
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
    Router. json_schema mode keeps output valid at T=1.0 for non-Gemini."""
    return ChatLiteLLMRouter(
        router = _get_router(), 
        model = REDUCE_LABEL_GROUP, 
        temperature = 1.0)


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
    trimmed to enabled∩selected."""
    if _dynamic_catalog_initialized and _dynamic_entries.get("dd-all"):
        return _apply_selection_filter(_dynamic_entries["dd-all"])
    return _apply_selection_filter(_all_entries())


def _synth_entries_current() -> list:
    """Active dd-synth catalog — dynamic if available, else static fallback;
    trimmed to enabled∩selected."""
    if _dynamic_catalog_initialized and _dynamic_entries.get("dd-synth"):
        return _apply_selection_filter(_dynamic_entries["dd-synth"])
    return _apply_selection_filter(_synth_entries())


def _reduce_label_entries_current() -> list:
    """Active dd-reduce-label catalog — dynamic if available, else static;
    trimmed to enabled∩selected."""
    if _dynamic_catalog_initialized and _dynamic_entries.get("dd-reduce-label"):
        return _apply_selection_filter(_dynamic_entries["dd-reduce-label"])
    return _apply_selection_filter(_reduce_label_entries())


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
