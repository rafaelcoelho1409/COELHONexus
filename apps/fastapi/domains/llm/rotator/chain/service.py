"""
Unified LLM Router — LiteLLM-backed with fail-fast pre-call checks.

DESIGN 2026-04-24: ONE ranked catalog (`dd-all`) reused by every KD step.
Previously had 5 separate groups (synth/refine/scope/curator/general); this
file was refactored to serve a SINGLE best→worst ordering so every node in
the KD graph draws from the same top-quality pool. Temperature differs per
step (T=0.7 for Self-Refine exploration; T=0.0 elsewhere), but the model
priority is identical. Rationale: all providers are free-tier, quality is
the only optimization objective — tokens and latency aren't costs here.

LiteLLM Router features:
  - `enable_pre_call_checks=True` — cooled-down deployments are filtered
    from the candidate pool BEFORE the call fires (0ms skip, no wasted
    wall-clock on known-bad providers like NIM's degraded glm-5.1)
  - `allowed_fails_policy` per error type — 413/401/timeout quickly
    cooldown; 429 gets short cooldown; internal 5xx gets short cooldown
  - `cooldown_time=60s` auto-recovery via Redis TTL (shared across all
    Celery workers)
  - `routing_strategy="simple-shuffle"` — best for production per LiteLLM
    docs (no Redis round-trips per call like usage-based-routing adds)

PROVIDERS (catalog re-verified 2026-04-24 against each provider's live
models + fresh benchmark data — LMArena, AAII, SWE-Bench Verified, AIME,
MMLU-Pro, LiveCodeBench, HumanEval, GPQA):

  NIM=12 | Mistral=6 | SambaNova=5 | Groq=4 | Gemini=4 | Cerebras=2 |
  DeepSeek=2    →   35 entries total, 7 providers

  Major 2026-04-24 changes:
  - DeepSeek V4 launched TODAY — swapped `deepseek-reasoner`/`deepseek-chat`
    (retire 2026-07-24) for `deepseek-v4-pro`/`deepseek-v4-flash`.
  - NIM re-verified: added gpt-oss-120b, kimi-k2-thinking, llama-4-maverick,
    minimax-m2.5, deepseek-v3_1-terminus; dropped `z-ai/glm4.7` (superseded
    by glm-5.1). glm-5.1 demoted within tier 1 due to NIM endpoint flakiness
    (forum threads 366610, 367453) — Router's fail-fast skips when degraded.
  - Groq re-verified: added `moonshotai/kimi-k2-instruct` (256K ctx).
  - SambaNova reaffirmed: MiniMax-M2.5 retains #1 on SWE-Bench Verified
    (80.2%) — ties Opus 4.6, beats GPT-5.2.
  - Mistral: moved `magistral-medium-latest` up to tier-1 (reasoning).
  - Gemini: 3.1-flash-lite-preview ranked above 2.5-flash on AAII delta.

SECURITY PIN (2026-04-23): litellm pinned to 1.83.12. v1.82.7 and v1.82.8
were compromised via supply-chain attack on 2026-03-24 (Trivy CI/CD breach).
DO NOT allow `litellm>=1.82.7,<1.83.0`. v1.83.0+ ships from LiteLLM's
rebuilt CI/CD v2 pipeline (isolated envs, signed artifacts).

Interleaving: NO 3 same-provider in a row at ANY position. A single-
provider outage affects ≤ 2 top-10 entries.

Factories (all serve from the same `dd-all` group, varying only temperature):
  - build_llm_fallback_chain          — T=0.0
  - build_resolver_llm_chain           — T=0.0
  - build_synth_fallback_chain         — T=0.0 (synthesize_chapter, curator)
  - build_refine_llm_chain             — T=0.7 (Self-Refine per Madaan 2023)
  - build_curator_llm                  — T=0.0
"""
from __future__ import annotations
import logging
import redis
import redis.asyncio as redis_aio
import time
import httpx
import os
import re
import asyncio

# Hoisted from former in-function lazy imports (no cycle — none of these import chain).
from domains.llm.rotator import benchmarks, discovery, bandit

import litellm
from litellm import Router
from litellm.types.router import (
    RetryPolicy,
    AllowedFailsPolicy,
    ModelGroupInfo,
)
from langchain_litellm.chat_models import ChatLiteLLMRouter


from .constants import (
    KEYLM_GROUP,
    REDUCE_LABEL_GROUP, 
    SYNTH_GROUP,
    DD_EMBED_GROUP,
    DD_EMBED_MODEL_NAME,
    DD_EMBED_BATCH_SIZE,
    GROUP,
    _JUDGE_KD_PROCESS,
    _JUDGE_BANDIT_TOP_K,
    _JUDGE_EXPECTED_LATENCY_S,
    DD_RERANK_MODEL_NAME,
    _NIM_RERANK_BASE,
    _PROVIDER_CHAPTER_CAPS,
    _router_instance,
    _pinned_chain_cache,
    _pinned_to_parent,
    _dynamic_entries,
    _dynamic_catalog_initialized
)


logger = logging.getLogger(__name__)


# =============================================================================
# OpenTelemetry callback (2026-05-12 night)
# =============================================================================
# When OTEL_EXPORTER_OTLP_ENDPOINT is set (handled by core.otel_setup at
# process startup), enabling the `otel` callback makes LiteLLM emit a span
# per LLM call containing:
#   - model (e.g. "moonshotai/kimi-k2.6")
#   - deployment_id (UUID assigned by Router to this entry)
#   - provider, custom_llm_provider
#   - request input/output token counts + computed cost
#   - latency (start_time → end_time)
#   - error type if call failed (RateLimitError, TimeoutError, etc.)
# Spans are sent through the SAME TracerProvider configured in otel_setup
# (dual-export: Alloy gRPC → LGTM + LangFuse HTTP OTLP), so per-deployment
# performance data lands in Mimir for routing-decision queries AND in
# LangFuse for prompt-replay UX. No per-call code change required.
#
# Adds `dd_process` metadata propagation: when a call site passes
# `config={"metadata": {"dd_process": "..."}}` to chain.ainvoke(...), LiteLLM
# attaches it as a span attribute. PromQL queries can then slice by
# (dd_process, deployment_id) to see "which model is fastest for section_synth".
if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
    try:
        litellm.callbacks = ["otel"]
        logger.info("[llm-chain] LiteLLM OTel callback enabled")
    except Exception as _ote:
        logger.warning(
            f"[llm-chain] failed to enable LiteLLM OTel callback "
            f"({type(_ote).__name__}: {_ote})"
        )


# =============================================================================
# Endpoints and provider credentials
# =============================================================================
def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


# =============================================================================
# Provider entry builders — each returns a LiteLLM model_list item
# =============================================================================
# `model_name` is the GROUP name the Router serves from.
# `litellm_params.model` uses LiteLLM's provider-prefixed naming.

# =============================================================================
# Unified ranked catalog — `dd-all`
# =============================================================================
# SAME list served to every KD step. Ordering is strictly best→worst by
# 2026-04-24 benchmark data (SWE-Bench Verified, MMLU-Pro, AIME-2025,
# LMArena Elo, AAII, LiveCodeBench, GPQA, HumanEval — composite).
# Providers interleaved so no 3 in a row of same provider anywhere.
def _groq_entry(group: str, model: str, timeout_s: int = 120) -> dict:
    return {
        "model_name": group,
        "litellm_params": {
            "model": f"groq/{model}",
            "api_key": _env("GROQ_API_KEY"),
            "timeout": timeout_s,
            "max_retries": 0,
        },
    }


def _nim_entry(group: str, model: str, timeout_s: int = 120) -> dict:
    # NIM is OpenAI-compatible; LiteLLM recognizes `nvidia_nim/<model>`.
    return {
        "model_name": group,
        "litellm_params": {
            "model": f"nvidia_nim/{model}",
            "api_key": _env("NVIDIA_API_KEY"),
            "timeout": timeout_s,
            "max_retries": 0,
        },
    }


def _cerebras_entry(group: str, model: str, timeout_s: int = 120) -> dict:
    return {
        "model_name": group,
        "litellm_params": {
            "model": f"cerebras/{model}",
            "api_key": _env("CEREBRAS_API_KEY"),
            "timeout": timeout_s,
            "max_retries": 0,
        },
    }


def _mistral_entry(group: str, model: str, timeout_s: int = 120) -> dict:
    return {
        "model_name": group,
        "litellm_params": {
            "model": f"mistral/{model}",
            "api_key": _env("MISTRAL_API_KEY"),
            "timeout": timeout_s,
            "max_retries": 0,
        },
    }


def _gemini_entry(group: str, model: str, timeout_s: int = 120) -> dict:
    return {
        "model_name": group,
        "litellm_params": {
            "model": f"gemini/{model}",
            "api_key": _env("GOOGLE_API_KEY"),
            "timeout": timeout_s,
            "max_retries": 0,
        },
    }


def _deepseek_entry(group: str, model: str, timeout_s: int = 120) -> dict:
    return {
        "model_name": group,
        "litellm_params": {
            "model": f"deepseek/{model}",
            "api_key": _env("DEEPSEEK_API_KEY"),
            "timeout": timeout_s,
            "max_retries": 0,
        },
    }


def _sambanova_entry(group: str, model: str, timeout_s: int = 120) -> dict:
    return {
        "model_name": group,
        "litellm_params": {
            "model": f"sambanova/{model}",
            "api_key": _env("SAMBANOVA_API_KEY"),
            "timeout": timeout_s,
            "max_retries": 0,
        },
    }


# =============================================================================
# Small-LM group — `dd-keylm` (KeyLLM cluster labeling for the classical MAP)
# =============================================================================
# Tiny instruct LMs (≤1B) for short-output format-strict tasks like the
# 2-4 word cluster titles emitted by graphs/knowledge/classical_map.py's
# KeyLLM step. NOT for synthesis — those go to GROUP=dd-all.
#
# Why a separate group: the dd-all rotator's frontier 70B+ models would
# burn unnecessary tokens on a 16-token title. Routing tiny tasks to a tiny
# model keeps latency < 200ms and leaves the rotator's free-tier RPM
# budget for the synthesis-heavy steps.
#
# Rationale for picks (research-validated 2026-05-09):
# - meta/llama-3.2-1b-instruct on NIM (GA): IFEval 59.5 (highest among
#   ≤1B candidates), temp=0 deterministic-stable, distilled from
#   Llama-3.1-405B/70B → strong format adherence.
# - llama-3.2-1b-preview on Groq (preview): same model on LPU silicon —
#   sub-100ms latency. Preview status is a stability risk so secondary.
def _keylm_entries() -> list:
    """
    Two-deep small-LM rotator for cluster labeling.

    Both deployments are NIM (Groq decommissioned llama-3.2-1b-preview, and
    NIM-only matches the embeddings architecture rule). The 3B variant is
    the fallback because:
      - First Terragrunt MAP run (2026-05-09) hit NIM's 40 RPM limit during
        the 28-cluster burst on the 1B endpoint → simple-shuffle had no
        alternate, ~10 cluster labels fell back to slug names.
      - Llama-3.2-3B-Instruct is the next size up Meta ships, also GA on
        NIM, identical chat template — drop-in fallback when 1B cools down.
      - 3B is slower (~2-3× wall time per call) but correctness > speed
        for the few-percent cluster labels that route to fallback.
    """
    return [
        # Primary: 1B for speed
        _nim_entry(
            KEYLM_GROUP, 
            "meta/llama-3.2-1b-instruct", 
            timeout_s = 30),
        # Fallback: 3B when 1B is cooled down (rate-limit absorber)
        _nim_entry(
            KEYLM_GROUP, 
            "meta/llama-3.2-3b-instruct", 
            timeout_s = 45),
    ]


# =============================================================================
# REDUCE labeling group — `dd-reduce-label` (Clio per-meta naming + ordering)
# =============================================================================
# Curated pool for the Clio REDUCE step's `_label_one` parallel calls and the
# single `order_chain` call (apps/fastapi/graphs/knowledge/reduce_cluster.py).
# Each call is ~3K tokens, structurally a classification task ("name this
# group of 30 related topics"). Co-mingling these with dd-all's reasoning
# models (Kimi K2-Thinking, GLM-5.1, MiniMax-M2.7, Qwen3.5-397B, DeepSeek-V3.2,
# Magistral, Gemini-3-flash R-mode) is the root cause of the 504s/stragglers/
# silent-None failures observed pre-2026-05-11: those models burn the 300s
# NIM gateway budget on <think> blocks for what should be a sub-10s call.
#
# This group hard-excludes reasoning models. Pool curated against the
# live state of `_all_entries()` below — every entry here is one that's
# currently reachable AND non-reasoning AND has native function-calling or
# response_format support on its hosting provider.
#
# Specifically EXCLUDED-by-design vs dd-all:
#   - SambaNova (whole provider paywalled per 2026-04-24 Run-8)
#   - Cerebras gpt-oss-120b (this account 404; user's key lacks model access)
#   - DeepSeek V4 (Insufficient Balance per dd-all comments)
#   - Groq gpt-oss-120b (8K TPM ceiling) and qwen3-32b (6K TPM) — also
#     excluded from dd-all for the same reason
#   - All reasoning-mode models even when reachable (the whole point)
#
# Pool sized for M=8-12 parallel labeling fanout. With 8 deployments and
# per-deployment cooldown, a single bad provider takes out ≤1 entry.
def _reduce_label_entries() -> list:
    """
    Non-reasoning rotator for the REDUCE step's labeling + ordering calls.

    Order: fastest LPU/TPU silicon first (Groq, Gemini Flash-Lite), then
    NIM hybrid-Mamba + gpt-oss + Mistral-Large-3, then Mistral direct,
    then Llama-4 Maverick as deep tail. ~8 deployments — generous
    cooldown-redundancy for parallel labeling.

    Timeouts are tighter than dd-all (60-90s vs 120s) — these calls are
    structurally short; a long delay almost always means a flaky model
    head and we'd rather fall through faster than wait it out.
    """
    return [
        # --- Tier 1: LPU/TPU silicon, sub-100ms TTFT, native tools ---
        # Groq llama-3.3-70b-versatile is EXCLUDED from dd-all only because of
        # the code-gen error benchmark; chapter naming doesn't generate code,
        # so that exclusion doesn't apply here. 12K TPM ample for ~3K prompts.
        _groq_entry(
            REDUCE_LABEL_GROUP, 
            "llama-3.3-70b-versatile", 
            timeout_s = 60),
        # Gemini 3.1 Flash-Lite preview: 381 t/s, AAII 34, 1M ctx, native tools.
        # Distinct from `gemini-2.5-flash-lite` which is disabled in dd-all
        # (returns empty choices on the complex ChapterOutput schema) — the
        # REDUCE schemas (MetaLabelDraft / OrderedIndices) are much simpler.
        # Gemini-3 requires T=1.0 (Google's API: "Setting temperature < 1.0
        # for Gemini 3 models can cause infinite loops, degraded reasoning
        # performance, and failure on complex tasks"). Polish #5b (2026-05-11):
        # the factory `build_reduce_label_chain` now passes T=1.0 to ALL
        # dd-reduce-label deployments — call-time temperature wins over
        # deployment-level litellm_params in LiteLLM Router, so the per-
        # deployment override approach we tried first didn't take effect.
        # json_schema mode keeps output valid at T=1.0 for non-Gemini
        # deployments too; only sampling among valid JSON paths differs.
        # Bumped 2026-05-13 from -preview (retires 2026-05-25) to stable
        # (shipped 2026-05-07; ai.google.dev/gemini-api/docs/deprecations).
        _gemini_entry(
            REDUCE_LABEL_GROUP, 
            "gemini-3.1-flash-lite", 
            timeout_s = 60),
        # --- Tier 2: NIM-hosted, non-reasoning, high-context ---
        # Nemotron-3-super-120b-a12b: 1M ctx hybrid Mamba, AAII 36, leads
        # size class on AIME-2025 + Terminal-Bench. Non-reasoning by default
        # (no detailed_thinking parameter exposed by the NIM endpoint).
        _nim_entry(
            REDUCE_LABEL_GROUP, 
            "nvidia/nemotron-3-super-120b-a12b", 
            timeout_s = 90),
        # gpt-oss-120b on NIM — Groq's 8K TPM ceiling makes Groq's host
        # permanently incompatible; Cerebras 404s on this account; NIM is
        # the only viable host for the gpt-oss family on this account.
        _nim_entry(
            REDUCE_LABEL_GROUP, 
            "openai/gpt-oss-120b", 
            timeout_s = 90),
        # Mistral Large 3 via NIM (DUP of Mistral direct below, NIM infra
        # adds an independent failure domain).
        _nim_entry(
            REDUCE_LABEL_GROUP, 
            "mistralai/mistral-large-3-675b-instruct-2512", 
            timeout_s = 90),
        # --- Tier 3: Mistral direct API ---
        # Mistral Large 3 v25.12 — LMArena #2 OSS, native function calling,
        # 256K ctx. Same model as the NIM entry above; different infra.
        _mistral_entry(
            REDUCE_LABEL_GROUP, 
            "mistral-large-latest", 
            timeout_s = 90),
        # Mistral Small 4 v26.03 — HumanEval 92, MMLU 88.5, AAII 28 — outperforms
        # Mistral Medium 3.1 on most benchmarks; fastest viable fallback here.
        _mistral_entry(
            REDUCE_LABEL_GROUP, 
            "mistral-small-latest", 
            timeout_s = 60),
        # --- Tier 4: deep tail ---
        # Llama-4 Maverick 17B-128E MoE on NIM — 1M ctx, AAII 18; weak relative
        # to the head of the pool but absorbs cooldown bursts when everything
        # above is in cooldown. Same-token-budget cost as the tier-1 entries.
        _nim_entry(
            REDUCE_LABEL_GROUP, 
            "meta/llama-4-maverick-17b-128e-instruct", 
            timeout_s = 90),
    ]


# =============================================================================
# Section-synth prose group — `dd-synth` (Scope B, 2026-05-12 night)
# =============================================================================
# Curated pool for the hierarchical_synth Phase C per-section synthesis calls
# (8 sections/chapter × N self-refine iters × ~7 chapters parallel = 16-48
# concurrent at peak). Section synth is structurally PROSE GENERATION with
# embedded code blocks — NOT classification, NOT reasoning. Reasoning models
# burn the 300s NIM gateway budget on <think> blocks for what should be
# straight prose; that was the root cause of the 2026-05-12 cascade-
# exhaustion observation (ch01 OP-12 rescue path, score=0.00 / 0 iters).
#
# Hybrid pool revised 2026-05-12 night-late: previous pure-non-reasoning curation
# surfaced a NEW failure mode during Scope B E2E (study 8f6af2b8) — the
# smaller non-reasoning models (Mistral Medium, Llama-4 Maverick) DROP HASHES
# under heavy ChapterOutput schemas. Observed on ch01 iter 2: 29 of 52 vault
# hashes MISSING in the structured output (56% drop rate), 7 duplicated, 5
# thin sections. Each refine iter made it WORSE because different pool members
# produced different incomplete subsets.
#
# Root cause: non-reasoning models have shorter effective output token
# budgets and weren't trained as rigorously on multi-entity structured
# outputs as the frontier reasoning models (Kimi K2.6, GLM-5.1, MiniMax M2.7).
# Reasoning models, when given the Scope B concurrency cap (DD_LLM_GLOBAL_
# CONCURRENCY=10) preventing peak-cascade exhaustion, complete structured
# output correctly because their per-call timeout is buffered by the cap.
#
# Design tradeoff now:
#   - Tier 1 (REASONING — strong structured output): the four frontier
#     reasoning models from dd-all. Each call burns <think> tokens before
#     emitting parseable output (300s NIM gateway), but the concurrency cap
#     stops peak parallelism from exhausting the cascade. Output is
#     structurally complete (52/52 hashes survive the audit).
#   - Tier 2 (NON-REASONING FRONTIER): Mistral L3 + Nemotron-3-super +
#     gpt-oss-120b. Fast, complete structured output on chapters where the
#     reasoning models are cooling down.
#   - Tier 3 (DEEP TAIL): Mistral Medium + Mistral Small + Llama-4 Maverick.
#     Fast cooldown absorbers; OK for small chapters but drop hashes on
#     large schemas — kept as last-resort cascade members only.
#
# Hard EXCLUSIONS (vs dd-all):
#   - Gemini 3.x family entirely: Google's own warning per Gemini 3 docs —
#     "Setting temperature < 1.0 can cause infinite loops, degraded reasoning
#     performance, and failure on complex tasks." Section synth runs at
#     T=0.0/0.7; either incompatible.
#   - Magistral (Mistral's R-mode by default — kept its non-reasoning sibling
#     Mistral Medium instead).
#   - Cerebras whole provider (this account 404s on every model).
#   - SambaNova whole provider (paywalled).
#   - DeepSeek direct API (Insufficient Balance).
#   - Groq gpt-oss-120b + Groq llama-3.3-70b-versatile (8-12K TPM ceiling —
#     section synth prompts hit 21-37K tokens; same TPM-too-tight pattern).
#
# All models support native function_calling AND response_format=json_schema
# on their hosting provider — required for ChapterOutput / ProseChapterOutput
# / Section structured output reliability.
def _synth_entries() -> list:
    """
    Hybrid reasoning + non-reasoning rotator for hierarchical_synth Phase C.

    Revised 2026-05-12 night-late after E2E study 8f6af2b8 revealed that
    pure-non-reasoning pool drops 56% of vault hashes on large chapters.
    Reasoning models go FIRST because their structured-output completeness
    is what the post-synth audit gate requires; non-reasoning models stay
    as Tier 2/3 cascade fallback for cooldown absorption.

    Concurrency cap (DD_LLM_GLOBAL_CONCURRENCY=10 in helpers.py) prevents
    peak parallelism from cascade-exhausting via reasoning-model <think>
    timeouts; that's what makes Tier 1 reasoning models viable here at all.
    """
    return [
        # --- Tier 1: Frontier reasoning (strong structured output) ---
        # Kimi K2.6 on NIM — AAII 49, 256K ctx. Non-thinking K2 variant; Moonshot's
        # current K2.x on NIM (K2.5 EOL 2026-04-30, K2-Thinking EOL 2026-05-12).
        _nim_entry(
            SYNTH_GROUP, 
            "moonshotai/kimi-k2.6", 
            timeout_s = 180),
        # GLM-5.1 on NIM — AAII 51 (Reasoning), SWE-Bench Pro 58.4% (#1 OSS).
        _nim_entry(
            SYNTH_GROUP, 
            "z-ai/glm-5.1", 
            timeout_s = 180),
        # MiniMax M2.7 on NIM — AAII 50, 204K ctx agentic, SWE-Pro 56.22%.
        _nim_entry(
            SYNTH_GROUP, 
            "minimaxai/minimax-m2.7", 
            timeout_s = 180),
        # DeepSeek V4-Flash on NIM — AAII 47, free-tier path (DeepSeek direct
        # paywalled). Re-enabled 2026-05-13 as V3.2 EOL successor.
        _nim_entry(
            SYNTH_GROUP, 
            "deepseek-ai/deepseek-v4-flash", 
            timeout_s = 180),
        # --- Tier 2: Frontier non-reasoning (complete structured output, fast) ---
        # Mistral Large 3 v25.12 direct — LMArena #2 OSS non-reasoning, 256K ctx.
        _mistral_entry(
            SYNTH_GROUP, 
            "mistral-large-latest", 
            timeout_s = 120),
        # Mistral Large 3 via NIM (independent failure domain from Mistral direct).
        _nim_entry(
            SYNTH_GROUP, 
            "mistralai/mistral-large-3-675b-instruct-2512", 
            timeout_s = 120),
        # Nemotron-3-super-120b-a12b: 1M ctx hybrid Mamba, AAII 36.
        _nim_entry(
            SYNTH_GROUP, 
            "nvidia/nemotron-3-super-120b-a12b", 
            timeout_s = 120),
        # gpt-oss-120b on NIM — AAII 33, native tools.
        _nim_entry(
            SYNTH_GROUP, 
            "openai/gpt-oss-120b", 
            timeout_s = 120),
        # --- Tier 3: Deep-tail cooldown absorbers (may drop hashes on large schemas) ---
        # Mistral Medium 3.1 — between Large and Small.
        _mistral_entry(
            SYNTH_GROUP, 
            "mistral-medium-latest", 
            timeout_s = 120),
        # Mistral Small 4 v26.03 — HumanEval 92, AAII 28. Fast fallback; OK for small chapters.
        _mistral_entry(
            SYNTH_GROUP, 
            "mistral-small-latest", 
            timeout_s = 90),
        # Llama-4 Maverick 17B-128E MoE on NIM — 1M ctx, deep tail.
        _nim_entry(
            SYNTH_GROUP, 
            "meta/llama-4-maverick-17b-128e-instruct", 
            timeout_s = 120),
    ]


# =============================================================================
# Embedding group — `dd-embed` (vector embeddings for KD MAP/REDUCE/preview)
# =============================================================================
# **SINGLE-ENTRY by design** — embedding rotation across providers breaks
# cosine geometry within a study (different model = different vector space).
# If NIM is down for an extended period, the LiteLLM Router's per-deployment
# cooldown + retry policy handles transient failures (5xx/429/timeout) on
# the same deployment; longer outages = study fails, user retries (cheap).
# Don't add a second model to this group — see project_planner_map_replacement
# memory for the regression that motivated this rule.
#
# Pick rationale (rev 2026-05-17 night — reverted from 8b attempt):
# - llama-embed-nemotron-8b was attempted 2026-05-17 (MTEB v2 leader on paper)
#   but is NOT exposed via NIM's hosted free-tier API (`integrate.api.nvidia.com/v1/models`
#   confirms it's absent — exists only on HuggingFace + as a self-deployable
#   NIM container). Reverted to llama-nemotron-embed-1b-v2 which IS hosted.
# - 40 RPM, NO monthly cap (Mistral's 2 RPM too tight for bulk filter)
# - Commercial license OK (Jina v4 is non-commercial — license trap)
# - Same NVIDIA_API_KEY already in use for the LLM rotator
# - Higher MTEB rank than bge-m3 / e5-mistral at sub-2B size class
# - 2048-dim is plenty (downstream cluster/refine consume the unit-norm vectors)
# - Cache invalidation: DD_EMBED_MODEL_NAME below feeds the embed_corpus
#   cache hash so any stored .npz blobs from a different model re-embed cleanly.
def _embed_entries() -> list:
    """
    Single-entry embedding group — see DD_EMBED_GROUP docstring above.

    NIM-specific params: `encoding_format` is required (NIM 400s without it).
    `input_type` is required for ASYMMETRIC models — but it differs per call
    (passage for corpus indexing, query for anchor/retrieval), so we DON'T
    bake it here; callers pass it through `embed_via_router_async(..., input_type=)`.
    The default behavior remains "passage" to preserve existing callers.
    """
    return [
        {
            "model_name": DD_EMBED_GROUP,
            "litellm_params": {
                "model":           f"nvidia_nim/{DD_EMBED_MODEL_NAME}",
                "api_key":         _env("NVIDIA_API_KEY"),
                "timeout":         120,
                "max_retries":     0,
                "encoding_format": "float",
            },
        },
    ]


def embed_via_router_sync(
    texts: list[str], 
    input_type: str = "passage",
) -> list[list[float]]:
    """
    Sync batch-embed via the rotator's `dd-embed` group. Auto-batches at
    DD_EMBED_BATCH_SIZE; returns vectors in input order.

    `input_type`: "passage" for documents being indexed, "query" for short
    anchor/search strings. Asymmetric models (llama-embed-nemotron-8b family)
    have different encoding heads — using the wrong one silently costs 3-8
    cosine points (E5 / Nemotron model cards).

    Empty/whitespace inputs are substituted with " " to keep the OpenAI-style
    /v1/embeddings call happy (a real provider would 400 on empty inputs).
    """
    if not texts:
        return []
    router = _get_router()
    clean = [t if (t and t.strip()) else " " for t in texts]
    out: list[list[float]] = []
    for start in range(0, len(clean), DD_EMBED_BATCH_SIZE):
        batch = clean[start:start + DD_EMBED_BATCH_SIZE]
        # NIM's asymmetric embedding models REQUIRE input_type at every call
        # (passage for docs, query for anchors). `extra_body=` does NOT work
        # for LiteLLM embeddings (gets passed as a literal field, NIM rejects).
        response = router.embedding(
            model = DD_EMBED_GROUP,
            input = batch,
            encoding_format = "float",
            input_type = input_type,
        )
        # LiteLLM normalizes to OpenAI shape: response["data"] is a list of
        # {"embedding": [...], "index": N, "object": "embedding"}.
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
    """Async equivalent of embed_via_router_sync. See its docstring for
    `input_type` semantics.

    `on_batch`, when provided, is an async callback invoked after each
    batch completes with kwargs (n_done, n_total, batch_size). Used by
    embed_corpus to emit live progress events to the SSE channel. Errors
    in the callback are swallowed — the embedding work always takes
    precedence over progress reporting."""
    if not texts:
        return []
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
        )
        out.extend(item["embedding"] for item in response["data"])
        if on_batch is not None:
            try:
                await on_batch(
                    n_done = len(out), 
                    n_total = total, 
                    batch_size = len(batch),
                )
            except Exception:
                pass
    if len(out) != texts.__len__():
        raise RuntimeError(
            f"dd-embed: rotator returned {len(out)} vectors for {len(texts)} inputs"
        )
    return out


# =============================================================================
# Rerank — NIM cross-encoder via direct httpx (NOT LiteLLM)
# =============================================================================
# NIM rerankers live on a different domain + URL shape than the OpenAI-compat
# embedding endpoint, and the request/response payload is NIM-specific (not
# OpenAI-compat). LiteLLM's `router.arerank()` posts to OpenAI's `/v1/rerank`
# shape, which NIM returns 404 for — verified 2026-05-17 night via live probes.
#
# So `dd-rerank` is NOT a LiteLLM Router group. We call the NIM rerank API
# directly via httpx using the same NVIDIA_API_KEY as the rest of the rotator.
#
# Pick: `nvidia/llama-nemotron-rerank-1b-v2` — canonical NVIDIA-validated pair
# with the llama-nemotron-embed-1b-v2 family, 1B params, 40 RPM free-tier.
# Successor to `nvidia/llama-3.2-nv-rerankqa-1b-v2` (which deprecates 2026-05-18).
# Stronger alternative if 1B underperforms: `nvidia/nv-rerankqa-mistral-4b-v3`.
async def chat_judge_async(
    prompt: str,
    max_tokens: int = 8,
    temperature: float = 0.0,
) -> str:
    """Single-shot text-classification call via the dd-all big-LLM rotator.
    Returns the assistant's stripped text content. Uses LiteLLM Router's
    simple-shuffle routing — no per-call bandit learning.

    Kept for callers that just want a quick completion. Prefer
    `chat_judge_bandit_async` when the call is repeated (e.g. off_topic's
    per-page judgments) so the bandit can learn which deployments are
    reliable + steer traffic accordingly."""
    router = _get_router()
    response = await router.acompletion(
        model = GROUP,
        messages = [{"role": "user", "content": prompt}],
        temperature = temperature,
        max_tokens = max_tokens,
    )
    return (response.choices[0].message.content or "").strip()


# =============================================================================
# ParetoBandit-driven LLM judge — adaptive deployment selection with reward
# =============================================================================
# 2026-05-17 wiring: the off_topic node fires hundreds of binary KEEP/DROP
# calls per planner run. Routing every call through Router's simple-shuffle
# means we keep hitting deployments that consistently 429 or timeout. The
# bandit learns from each outcome and steers subsequent calls toward
# deployments that ACTUALLY answer quickly with parseable verdicts.
#
# Per 2026 SOTA literature (LLM Bandit arxiv 2502.02743, BaRP 2510.07429,
# IBM MAR AAAI 2026): contextual bandit feedback is the published best
# practice for partial-feedback LLM routing under cost+accuracy trade-offs.
async def _redis_for_bandit():
    """Lazily build a Redis client for ParetoBandit reads/writes. Returns
    None on env-misconfig so callers can fall back gracefully."""
    host = _env("REDIS_HOST")
    port = _env("REDIS_PORT", "6379")
    password = _env("REDIS_PASSWORD")
    if not host:
        return None
    url = (
        f"redis://:{password}@{host}:{port}"
        if password else f"redis://{host}:{port}"
    )
    try:
        return redis_aio.from_url(
            url, 
            socket_connect_timeout = 3.0, 
            socket_timeout = 5.0,
        )
    except Exception:
        return None


def _classify_error(exc: Exception) -> str:
    """Map a litellm/httpx exception to ParetoBandit's error_class taxonomy
    (see compose_reward in bandit.py for the reward magnitudes)."""
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    if "ratelimit" in name or "429" in msg or "rate limit" in msg:
        return "rate_limit"
    if "timeout" in name or "timed out" in msg:
        return "timeout"
    if "auth" in name or "401" in msg or "403" in msg or "invalid api key" in msg:
        return "auth_error"
    if "content" in name and "filter" in name:
        return "content_filter"
    if "5" in msg and ("server" in msg or "internal" in msg or "bad gateway" in msg):
        return "server_error"
    return "unknown"


async def chat_judge_bandit_async(
    prompt: str,
    *,
    max_tokens: int = 8,
    temperature: float = 0.0,
    timeout_s: float = 30.0,
    expected_pattern: str | None = None,
) -> tuple[str, dict]:
    """Bandit-routed single-shot text classification.

    Pipeline:
      1. Build context vector (dd_process=dd-grader, temporal sin/cos +
         provider error rates default to 0 — Phase 2 features).
      2. predict_top_k against the dd-all candidate set.
      3. Cascade through the ranked deployments: try top-1 via direct
         `litellm.acompletion(model=deployment_id)` (bypasses Router so
         we hit the SPECIFIC chosen deployment, not Router shuffle).
      4. After each attempt — success or failure — submit a reward
         signal so the bandit learns. `expected_pattern` (e.g. "^(KEEP|DROP)$")
         drives the schema_valid component of the reward.
      5. Return (response_text, meta) where meta has `deployment`,
         `latency_s`, `attempts`, `reward`.

    Falls back to Router-shuffle (`chat_judge_async`) on any infrastructure
    failure (Redis down, no candidates ranked, etc.) so the planner
    never wedges on a misconfigured bandit.
    """
    rds = await _redis_for_bandit()
    if rds is None:
        # No Redis = no bandit. Fall back to Router-shuffle.
        text = await chat_judge_async(
            prompt, 
            max_tokens = max_tokens, 
            temperature = temperature)
        return text, {
            "deployment": "router-shuffle", 
            "attempts": 0,
            "latency_s": None, "reward": None,
            "fallback": "no_redis"}
    candidates = [e["litellm_params"]["model"] for e in _all_entries_current()]
    ctx = bandit.make_context_vector(_JUDGE_KD_PROCESS)
    pattern = None
    if expected_pattern:
        pattern = re.compile(expected_pattern)
    try:
        ranked = await bandit.predict_top_k(
            _JUDGE_KD_PROCESS, 
            ctx, 
            candidates,
            redis = rds, 
            k = _JUDGE_BANDIT_TOP_K,
        )
    except Exception as e:
        logger.warning(f"[dd-judge-bandit] predict_top_k failed: {e}; "
                       f"falling back to router-shuffle")
        try:
            await redis.aclose()
        except Exception:
            pass
        text = await chat_judge_async(
            prompt, 
            max_tokens = max_tokens, 
            temperature = temperature)
        return text, {
            "deployment": "router-shuffle", 
            "attempts": 0,
            "latency_s": None, 
            "reward": None,
            "fallback": "predict_failed"}
    api_key = _env("NVIDIA_API_KEY") or _env("GROQ_API_KEY") or ""
    last_error: str | None = None
    attempts = 0
    try:
        for deployment_id, _ucb, _n_obs in ranked:
            attempts += 1
            # Resolve the right api_key for this deployment's provider.
            # _env lookups are cheap; do per-deployment so a Groq pick gets
            # GROQ_API_KEY etc. without leaking the wrong creds.
            provider = deployment_id.split("/", 1)[0] if "/" in deployment_id else ""
            provider_key_env = {
                "nvidia_nim": "NVIDIA_API_KEY",
                "groq":       "GROQ_API_KEY",
                "cerebras":   "CEREBRAS_API_KEY",
                "mistral":    "MISTRAL_API_KEY",
                "gemini":     "GEMINI_API_KEY",
                "openai":     "OPENAI_API_KEY",
                "deepseek":   "DEEPSEEK_API_KEY",
                "sambanova":  "SAMBANOVA_API_KEY",
            }.get(provider, "NVIDIA_API_KEY")
            api_key = _env(provider_key_env) or _env("NVIDIA_API_KEY") or ""
            t0 = time.monotonic()
            error_class: str | None = None
            success = False
            schema_valid = False
            response_text = ""
            try:
                response = await litellm.acompletion(
                    model = deployment_id,
                    api_key = api_key,
                    messages = [{"role": "user", "content": prompt}],
                    temperature = temperature,
                    max_tokens = max_tokens,
                    timeout = timeout_s,
                )
                response_text = (response.choices[0].message.content or "").strip()
                success = True
                if pattern is not None:
                    head = response_text.upper().split()[0].strip(".,;:!\"'`)") \
                        if response_text else ""
                    schema_valid = bool(pattern.match(head))
                else:
                    schema_valid = bool(response_text)
            except Exception as e:
                error_class = _classify_error(e)
                last_error = f"{type(e).__name__}: {str(e)[:120]}"
            latency_s = float(time.monotonic() - t0)
            reward = bandit.compose_reward(
                success = success,
                schema_valid = schema_valid,
                latency_s = latency_s,
                expected_latency_s = _JUDGE_EXPECTED_LATENCY_S,
                error_class = error_class,
            )
            try:
                await bandit.update(
                    deployment_id, 
                    _JUDGE_KD_PROCESS, 
                    ctx, 
                    reward, 
                    redis = rds,
                )
            except Exception:
                pass
            if success and schema_valid:
                return response_text, {
                    "deployment": deployment_id,
                    "attempts":   attempts,
                    "latency_s":  latency_s,
                    "reward":     reward,
                }
            # Success but bad schema: still return — caller will treat as
            # unparseable. Continuing the cascade would waste budget on a
            # quirk that more model swaps won't fix.
            if success:
                return response_text, {
                    "deployment": deployment_id,
                    "attempts":   attempts,
                    "latency_s":  latency_s,
                    "reward":     reward,
                    "schema_invalid": True,
                }
            # On failure: cascade to the next ranked deployment.
        # All ranked deployments failed.
        raise RuntimeError(
            f"dd-judge-bandit: all {attempts} ranked deployments failed; "
            f"last error: {last_error}"
        )
    finally:
        try:
            await redis.aclose()
        except Exception:
            pass


async def rerank_via_router_async(
    query: str, documents: list[str], top_n: int | None = None,
) -> list[tuple[int, float]]:
    """Cross-encoder rerank `documents` against `query` via NIM's hosted
    rerank API. Returns a list of (orig_index, relevance_score) pairs in
    descending score order. NIM rerankers return raw logits (typically
    [-12, +12]); caller is responsible for thresholding.

    Used by the off_topic substep on the GMM boundary band — too-narrow
    margin gets a second opinion from the cross-encoder instead of trusting
    the bi-encoder cosine alone.

    Direct httpx instead of the LiteLLM Router because NIM's rerank API
    isn't OpenAI-compat — see header comment for the verification details.
    """
    if not documents:
        return []
    api_key = _env("NVIDIA_API_KEY")
    if not api_key:
        raise RuntimeError(
            "dd-rerank: NVIDIA_API_KEY env var not set — required for NIM rerank"
        )
    # Model slug in URL strips the "nvidia/" prefix — that's the NIM
    # path convention (`/v1/retrieval/nvidia/{slug}/reranking`).
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
        resp = await client.post(
            url, 
            json = payload, 
            headers = headers)
        resp.raise_for_status()
        data = resp.json()
    # NIM response shape: {"rankings": [{"index": N, "logit": float}, ...]}
    # already sorted descending by logit. Map to our (idx, score) tuples.
    rankings = data.get("rankings") or []
    pairs = [(int(r["index"]), float(r["logit"])) for r in rankings]
    if top_n is not None:
        pairs = pairs[:top_n]
    return pairs


def _all_entries() -> list:
    """
    37-entry benchmark-ranked catalog across 8 free-tier providers.
    Served identically to synthesizer / refiner / scope-classifier /
    curator / general-purpose chains — no per-step catalog variation.
    Temperature is applied at the factory layer, not per entry.

    STRICT BENCHMARK ORDER (2026-04-24 refresh): ordered descending by
    Artificial Analysis Intelligence Index (AAII v4.0) with SWE-Bench
    Verified / LiveCodeBench / AIME-2025 as tiebreakers. Same-model-
    different-provider duplicates cluster together; within a tie the
    most reliable infrastructure is listed first (Cerebras > SambaNova
    > direct-API > NIM when NIM has known endpoint issues).

    No provider interleaving — user requested pure benchmark primacy:
    "the best LLMs first according to benchmarks, not 'this or that
    provider as a workhorse so their models come first'".

    Top-5 avg AAII ≈ 54 (Kimi K2 Thinking 67, V4-Pro 57, GLM-5.1 51,
    MiniMax-M2.7 50, Kimi K2-0905 49); bottom-5 avg ≈ 16 (Llama-3.3 14,
    Scout 15, Maverick×2 18, Magistral Small 18). Gap ≈ 38 AAII points
    — a full generation, not a gradient.
    """
    return [
        # --- 1–10: Frontier class (AAII 45+) ---
        # _nim_entry(GROUP, "moonshotai/kimi-k2-thinking", timeout_s=120),                         # DISABLED 2026-05-13 — NIM returns 410 "Gone" since 2026-05-12 EOL (surfaced via Phase 3.1 outline_compare validation). The K2-thinking line is the reasoning variant of K2; no in-family replacement yet from Moonshot on NIM. K2.6 (#3 below) is the non-reasoning K2 successor and remains active. AAII 67 was highest on the list — when Moonshot ships K2.7-thinking or equivalent on NIM, restore here.
        # _deepseek_entry(GROUP, "deepseek-v4-pro", timeout_s=120),                                # DISABLED 2026-04-24 — "Insufficient Balance" on account. Re-enable after top-up (5M free grant used up or V4 not in free tier). AAII ~57, 1T+ MoE FP4, 1M ctx
        _nim_entry(GROUP, "z-ai/glm-5.1", timeout_s=120),                                          # AAII 51 (Reasoning); SWE-Bench Pro 58.4% (#1 OSS); may be skipped during NIM endpoint flakiness
        _nim_entry(GROUP, "minimaxai/minimax-m2.7", timeout_s=120),                                # AAII 50 — 204K ctx agentic, SWE-Pro 56.22%, SWE-Multilingual 76.5
        # _groq_entry(GROUP, "moonshotai/kimi-k2-instruct", timeout_s=120),                        # DISABLED 2026-04-24 — not in Groq's actual catalog (research agent hallucinated; Groq listing confirmed missing). AAII 49 (K2-0905), 256K ctx
        # _deepseek_entry(GROUP, "deepseek-v4-flash", timeout_s=120),                              # DISABLED 2026-04-24 — "Insufficient Balance" (same DeepSeek account as v4-pro). AAII 47 (Max), 284B MoE, 1M ctx
        _nim_entry(GROUP, "moonshotai/kimi-k2.6", timeout_s=120),                                  # RE-ENABLED 2026-05-13 — K2.5 EOL'd on NIM 2026-04-30 (surfaced via Phase 3.1 outline_compare 410 cascade); K2.6 is the current Kimi K2.x on NIM (build.nvidia.com/moonshotai/kimi-k2.6) and the original disable reason no longer applies. Sits in the cascade alongside K2-Thinking (#1, reasoning variant).
        _gemini_entry(GROUP, "gemini-3-flash-preview", timeout_s=120),                             # AAII 46 (R) / 35 (NR) — LiveCodeBench 90.8%, SWE-bench 78%, 1M ctx
        _nim_entry(GROUP, "qwen/qwen3.5-397b-a17b", timeout_s=120),                                # AAII 45 (R) / 40 (NR) — MMLU-Pro 87.18%, 262K ctx
        _nim_entry(GROUP, "deepseek-ai/deepseek-v4-flash", timeout_s=120),                          # BUMPED 2026-05-13 V3.2→V4-Flash — NIM EOL'd v3.2 on 2026-05-04 (surfaced via Phase 3.1 outline_compare 410 cascade, same week as v3.1-terminus). V4-Flash is the successor on the same free-tier NIM DeepSeek line per build.nvidia.com/deepseek-ai/deepseek-v4-flash (the `deepseek/` provider entries for V4-Flash stay commented because DeepSeek's own API is paywalled; NIM-hosted V4-Flash is the free-tier path).
        # --- 11–17: Strong second tier (AAII 34–42) ---
        # _sambanova_entry(GROUP, "MiniMax-M2.7", timeout_s=120),                                  # COMMENTED 2026-05-13 — model string updated M2.5→M2.7 (M2.7 supersedes M2.5 on SambaNova). Still DISABLED via "A payment method is required" SambaNova paywall (since 2026-04-24); re-enable after adding payment method. AAII 50, 204K ctx (M2.7).
        _nim_entry(GROUP, "minimaxai/minimax-m2.7", timeout_s=120),                                # RE-ENABLED 2026-05-13 — M2.5 EOL'd on NIM 2026-05-12; M2.7 is the successor (same as #3 active above — deliberate cascade duplicate so LiteLLM Router can rotate between the two instances if one hits a transient hiccup).
        # _cerebras_entry(GROUP, "zai-glm-4.7", timeout_s=120),                                    # DISABLED 2026-04-24 — 404 "you do not have access to it" (model exists in Cerebras catalog but API key lacks access). AAII 42 (R) / 34 (NR), SOTA τ²-Bench, 200K ctx, 355B params
        _nim_entry(GROUP, "nvidia/nemotron-3-super-120b-a12b", timeout_s=120),                     # AAII 36 — 1M ctx, hybrid Mamba, leads size class on AIME-2025 + Terminal-Bench
        # _sambanova_entry(GROUP, "DeepSeek-V3.1", timeout_s=120),                                 # DISABLED 2026-04-24 (Run-8 evidence) — full SambaNova account now paywalled; whole provider returns "A payment method is required" even for previously-free models. AAII 34 (R) when/if re-enabled.
        _nim_entry(GROUP, "deepseek-ai/deepseek-v4-flash", timeout_s=120),                          # RE-ENABLED 2026-05-13 (cascade dup) — V3.1-Terminus → V3.2 → V4-Flash chain: V3.1-Terminus EOL 2026-05-04; V3.2 also EOL 2026-05-04 (NIM EOL'd them as a pair). V4-Flash is the successor (build.nvidia.com/deepseek-ai/deepseek-v4-flash). Deliberate cascade duplicate of the V4-Flash entry above for redundancy.
        _gemini_entry(GROUP, "gemini-3.1-flash-lite", timeout_s=90),                               # AAII 34 — GPQA Diamond 86.9%, 381 t/s, 1M ctx. Bumped 2026-05-13 from -preview (retires 2026-05-25) to stable (shipped 2026-05-07; ai.google.dev/gemini-api/docs/deprecations)
        # --- 18–21: gpt-oss-120b on four providers (AAII 33 each) ---
        # _cerebras_entry(GROUP, "gpt-oss-120b", timeout_s=120),                                   # DISABLED 2026-04-24 — 404 "you do not have access to it" (model listed in Cerebras catalog but key unauthorized). AAII 33, MMLU-Pro 90.0%, 3000 tok/s
        # _sambanova_entry(GROUP, "gpt-oss-120b", timeout_s=120),                                  # DISABLED 2026-04-24 — SambaNova response: "A payment method is required". Same model as #18 family
        # _groq_entry(GROUP, "openai/gpt-oss-120b", timeout_s=120),                                # DISABLED 2026-04-24 (OP-3) — 8K TPM ceiling permanently incompatible with 30K-token chapter prompts. Run-8 logged every call returning BadRequest("Request too large: Limit 8000, Requested 34127"). AAII 33 still served via NIM's `openai/gpt-oss-120b` entry.
        _nim_entry(GROUP, "openai/gpt-oss-120b", timeout_s=120),                                   # AAII 33 — DUP family; confirmed working on NIM
        # --- 22–23: AAII 30 ---
        _gemini_entry(GROUP, "gemini-2.5-flash", timeout_s=60),                                    # OP-25 (2026-04-25): timeout 120→60 — Gemini free tier is 20 req/DAY/model; once exhausted, stays exhausted ~24h. LiteLLM's 60s cooldown can't recover; shorter timeout at least makes the cascade walk past it faster instead of burning the outer 1200s budget. AAII ~30 — GPQA 82.8, MMLU-Lite 88.4, AIME 88, 1M ctx
        # --- 24–28: AAII 22–28 (Mistral cluster) ---
        _mistral_entry(GROUP, "mistral-small-latest", timeout_s=120),                              # AAII 28 — Mistral Small 4 v26.03, HumanEval 92, MMLU 88.5 (surprisingly > Medium 3.1)
        _mistral_entry(GROUP, "magistral-medium-latest", timeout_s=120),                           # AAII 27 — Magistral 1.2, AIME24 91.82%, GPQA-Diamond 76.3% (reasoning specialist)
        _mistral_entry(GROUP, "mistral-large-latest", timeout_s=120),                              # AAII 23 — Mistral Large 3 v25.12, LMArena #2 OSS, MATH-500 93.6, 256K ctx
        _nim_entry(GROUP, "mistralai/mistral-large-3-675b-instruct-2512", timeout_s=120),          # AAII 23 — DUP of #26 (same Large 3 model, NIM infra)
        # --- 29–31: AAII 21–22 ---
        _mistral_entry(GROUP, "devstral-medium-latest", timeout_s=120),                            # AAII 22 — Devstral 2 code-agents, SWE-Bench Verified 46.8%, 256K ctx
        # _gemini_entry(GROUP, "gemini-2.5-flash-lite", timeout_s=90),                             # DISABLED 2026-04-24 (OP-4) — returns empty `choices=[]` (0 completion tokens) when given our ChapterOutput tool schema; model can't produce structured output for the nested Section + Flashcard shape at the lite tier. Run-8 logged 14/14 BadRequest from LangChain's downstream parse of the empty response. Plain completion works fine, so NOT a credential / safety issue — structural tool-schema incompatibility. AAII 22/19.
        _mistral_entry(GROUP, "mistral-medium-latest", timeout_s=60),                              # OP-25 (2026-04-25): timeout 120→60 — Run-11 logs show frequent RateLimitError during synth+grader iters; shorter timeout demotes faster, lets cascade reach healthier entries. AAII 21 — Mistral Medium 3.1 v25.08, Arena top-10 overall
        # --- 32–37: Tail (AAII ≤20) ---
        # _groq_entry(GROUP, "qwen/qwen3-32b", timeout_s=120),                                     # DISABLED 2026-04-24 (OP-3) — 6K TPM ceiling; Run-8 logged repeated "Request too large: Limit 6000, Requested 33950" on chapter synth. Permanent incompat. AAII ~20 (tail-tier anyway).
        _mistral_entry(GROUP, "magistral-small-latest", timeout_s=120),                            # AAII 18 — Magistral Small 1.2, 24B reasoner
        # _sambanova_entry(GROUP, "Llama-4-Maverick-17B-128E-Instruct", timeout_s=120),            # DISABLED 2026-04-24 — same SambaNova account-wide paywall. AAII 18 when/if re-enabled.
        _nim_entry(GROUP, "meta/llama-4-maverick-17b-128e-instruct", timeout_s=120),               # AAII 18 — DUP of #34 (same Maverick, NIM infra, 1M ctx)
        # _groq_entry(GROUP, "meta-llama/llama-4-scout-17b-16e-instruct", timeout_s=120),          # DISABLED 2026-04-24 (OP-3) — 30K TPM barely covers our chapter-sized prompts; Run-8 logged 33615 / 30507 / 33950-token requests all rejected with "Request too large". Sometimes works when prompt is under 30K but unreliable. AAII ~15 (tail-tier). Same Llama-4 family served via SambaNova/NIM entries when those are enabled.
        # _sambanova_entry(GROUP, "Meta-Llama-3.3-70B-Instruct", timeout_s=120),                   # DISABLED 2026-04-24 — SambaNova response: "A payment method is required". AAII 14, 128K ctx on SambaNova
        # === DELIBERATELY EXCLUDED (verified 2026-04-24) ==================
        # Weak / context-tight / deprecated — skip to preserve quality
        #   - groq/openai/gpt-oss-20b (Run-7: 8K TPM < 30K prompts, permanent incompat)
        #   - groq/llama-3.1-8b-instant (weak for structured output)
        #   - groq/llama-3.3-70b-versatile (32% code-gen error benchmark)
        #   - groq/allam-2-7b (4K ctx)
        #   - groq/meta-llama/llama-4-maverick-17b-128e-instruct (RETIRED 2026-03-09 on Groq)
        #   - groq/deepseek-r1-distill-llama-70b (RETIRED 2025-10-02)
        #   - groq/mistral-saba-24b (RETIRED 2025-07-30)
        #   - groq/gemma2-9b-it (RETIRED 2025-10-08)
        # Gemini paid/deprecated
        #   - gemini-2.5-pro (PAYWALLED 2026-04-01)
        #   - gemini-3-pro-preview / gemini-3.1-pro-preview (paid-only)
        #   - gemini-2.0-flash / gemini-2.0-flash-lite (DEPRECATED, EOL 2026-06-01)
        # Cerebras deprecating 2026-05-27
        #   - cerebras/qwen-3-235b-a22b-instruct-2507
        #   - cerebras/llama3.1-8b (kept in nothing — weak + soon-dead)
        # DeepSeek retiring 2026-07-24 (replaced by V4 above)
        #   - deepseek-reasoner / deepseek-chat (shimmed to V4 internally — use V4 endpoints directly)
        #   - deepseek-coder (alias to chat, redundant)
        # Mistral edge / FIM / vision
        #   - ministral-{3b,8b,3-3b} (edge-only, not synth-grade)
        #   - codestral-latest (FIM completion only)
        #   - devstral-small-latest (marginal — devstral-medium covers same niche)
        #   - open-mixtral-8x22b (DEPRECATED 2025-03-30)
        #   - pixtral-* (vision-only)
        # NIM superseded / not-yet-live
        #   - nvidia_nim/z-ai/glm4.7 (superseded by glm-5.1 on NIM)
        # SambaNova context-tight
        #   - sambanova/Qwen3-32B (~40K ctx — borderline for 40K prompts)
        #   - sambanova/DeepSeek-V3.2 (8K ctx preview — DISQUALIFIED)
    ]


# =============================================================================
# Unified Router — single instance shared across all factories
# =============================================================================
def _get_router() -> Router:
    """
    Build the unified LiteLLM Router once per process. Shared state lives in
    Redis (cooldown cache + per-deployment TPM/RPM tracking) so all Celery
    workers see the same circuit-breaker state.
    """
    global _router_instance
    if _router_instance is not None:
        return _router_instance
    # Cascade + circuit-breaker policy
    # -------------------------------------------------------------------
    # In LiteLLM Router, `num_retries` is the CASCADE length: on failure,
    # the Router tries another deployment in the same group up to
    # num_retries additional times. Set to N-1 so a single request can
    # fall through the ENTIRE catalog if everything above fails.
    #
    # `retry_policy` caps the cascade per error type. Generous values so
    # every error class still cascades (setting to 0 here would DISABLE
    # the cascade for that error, which was the 2026-04-24 bug that
    # caused `NotFoundError` to abort instead of falling through).
    #
    # `allowed_fails_policy` is the CIRCUIT BREAKER (independent of
    # retries): after N failures within the window, cool down the
    # deployment for `cooldown_time` so the next request skips it at 0ms.
    # -------------------------------------------------------------------
    CASCADE_DEPTH = 40  # > total entries — ensures full catalog coverage
    retry_policy = RetryPolicy(
        AuthenticationErrorRetries = CASCADE_DEPTH,
        ContentPolicyViolationErrorRetries = CASCADE_DEPTH,
        RateLimitErrorRetries = CASCADE_DEPTH,
        BadRequestErrorRetries = CASCADE_DEPTH,
        TimeoutErrorRetries = CASCADE_DEPTH,
        InternalServerErrorRetries = CASCADE_DEPTH,
    )
    allowed_fails_policy = AllowedFailsPolicy(
        AuthenticationErrorAllowedFails = 0,    # invalid key = cooldown immediately
        BadRequestErrorAllowedFails = 1,        # 400/404/413 on first call = stop trying this model
        ContentPolicyViolationErrorAllowedFails = 2,
        RateLimitErrorAllowedFails = 1,         # 429 = cooldown immediately
        TimeoutErrorAllowedFails = 2,           # 2 timeouts → cooldown
        InternalServerErrorAllowedFails = 2,    # 5xx same
    )
    redis_kwargs = {}
    redis_host = _env("REDIS_HOST")
    if redis_host:
        redis_kwargs["redis_host"] = redis_host
        redis_kwargs["redis_port"] = int(_env("REDIS_PORT", "6379"))
        redis_password = _env("REDIS_PASSWORD")
        if redis_password:
            redis_kwargs["redis_password"] = redis_password
    _router_instance = Router(
        # Combined model_list — `dd-all` (synthesis/planner/critic), `dd-keylm`
        # (KeyLLM cluster labels), `dd-reduce-label` (Clio REDUCE per-meta
        # labeling + chapter ordering), and `dd-embed` (embeddings) all live
        # in one Router so they share the cooldown circuit-breaker + Redis
        # state. The factory + helper functions select which group via the
        # `model=` arg on ChatLiteLLMRouter / Router.embedding.
        model_list = (
            _all_entries_current()
            + _keylm_entries()
            + _reduce_label_entries_current()
            + _synth_entries_current()
            + _embed_entries()
        ),
        # simple-shuffle is recommended for production (LiteLLM docs): doesn't
        # add Redis round-trips per request the way usage-based-routing does.
        # Combined with allowed_fails_policy this effectively routes among
        # HEALTHY entries in priority order.
        routing_strategy = "simple-shuffle",
        enable_pre_call_checks = True,         # fail-fast core — skip cooled-down at 0ms
        allowed_fails = 3,                      # generic threshold (per-error policy overrides)
        allowed_fails_policy = allowed_fails_policy,
        cooldown_time = 60,                     # TTL for auto-recovery
        retry_policy = retry_policy,
        num_retries = CASCADE_DEPTH,            # cascade length — on failure, try another deployment up to 40 times
        set_verbose = False,
        **redis_kwargs,
    )
    # OP-LF-LITELLM-CALLBACK (2026-04-25 post-Run-16) — DISABLED 2026-04-25
    # mid-Run-17. The LiteLLM bundled langfuse integration
    # (`litellm/integrations/langfuse/langfuse.py:144`) reads
    # `langfuse.version.__version__` which DOES NOT EXIST on the langfuse v3+
    # SDK we have installed (the v3 module has no `.version` submodule;
    # `langfuse.__version__` directly OR `importlib.metadata.version` is the
    # new API). When `litellm.success_callback=["langfuse"]` is set, every
    # acompletion call eagerly initializes the langfuse logger and hits this
    # AttributeError. The errors are "Non-Blocking" per LiteLLM so calls
    # complete, but they spam logs AND emit zero traces (the integration
    # never finishes init). Net: pure noise, no cascade visibility.
    #
    # To re-enable: either (a) wait for LiteLLM upstream to fix the
    # `langfuse.version` reference (track at github.com/BerriAI/litellm),
    # OR (b) pin `langfuse<3` (would lose v4 features we already use), OR
    # (c) write a custom LiteLLM logger that calls our existing langfuse
    # client directly. Option (c) is the right path post-OP-HIERARCHICAL-
    # SYNTH if cascade visibility becomes critical.
    #
    # Cascade behavior is still observable via the existing LangChain
    # CallbackHandler (registered per-call via langfuse_config) — we lose
    # the per-provider attempt detail but keep the overall call timing +
    # cost (computed from token counts + LangFuse model price table).
    return _router_instance


# =============================================================================
# Public factory API — all serve from the same `dd-all` group
# =============================================================================
# Temperature is the only per-step variation. T=0.7 for Self-Refine
# exploration (Madaan 2023 §2); T=0.0 for deterministic calls elsewhere.
# Per-entry timeouts in the catalog reflect provider characteristics;
# the factory-level timeout args are kept for API compatibility only.
def build_llm_fallback_chain(
    groq_timeout_s: int = 120,
    nim_timeout_s: int = 300):
    """General-purpose LLM chain. Unified catalog at T=0.0."""
    return ChatLiteLLMRouter(
        router = _get_router(), 
        model = GROUP, 
        temperature = 0.0)


def build_resolver_llm_chain(
    groq_timeout_s: int = 30,
    nim_timeout_s: int = 60):
    """Resolver chain. Unified catalog at T=0.0."""
    return ChatLiteLLMRouter(
        router = _get_router(), 
        model = GROUP, 
        temperature = 0.0)


def build_synth_fallback_chain(
    groq_timeout_s: int = 120,
    nim_timeout_s: int = 300):
    """
    Synthesize_chapter + curator chain.

    Scope B (2026-05-12): when DD_USE_SYNTH_POOL=1, routes to the curated
    `dd-synth` non-reasoning pool instead of `dd-all`. Section synth is
    structurally prose generation — reasoning models burn the timeout on
    <think> blocks. The cascade exhaustion observed during the 2026-05-12
    E2E (ch01 OP-12 rescue, score=0.00 / 0 iters) was caused by the
    dd-all rotator routing parallel synth calls into reasoning-mode
    models that never produced a parseable response inside the deadline.
    See SYNTH_GROUP docstring above and Scope B research brief.
    Default "0" preserves the legacy dd-all routing.
    """
    use_synth_pool = os.environ.get(
        "DD_USE_SYNTH_POOL", "0",
    ).strip().lower() in ("1", "true", "yes")
    target_group = SYNTH_GROUP if use_synth_pool else GROUP
    return ChatLiteLLMRouter(
        router = _get_router(), 
        model = target_group, 
        temperature = 0.0,
    )


def build_synth_pool_chain():
    """
    Explicit factory for the dd-synth non-reasoning pool. Same as
    build_synth_fallback_chain() with DD_USE_SYNTH_POOL=1 — provided
    for direct use by callers that always want the synth pool
    regardless of env config (e.g. validation harnesses).
    """
    return ChatLiteLLMRouter(
        router = _get_router(), 
        model = SYNTH_GROUP, 
        temperature = 0.0,
    )


# =============================================================================
# Per-chapter model pinning (Fix #2 of Phase B/C audit-fail hardening, 2026-05-12 night)
# =============================================================================
# Problem: across refine iterations within a chapter, the rotator's simple-
# shuffle picks DIFFERENT deployments per iter. Each iter the LLM starts
# fresh with no memory of what the previous iter produced. The refiner's
# surgical "you missed hash X, Y, Z" feedback can't act on output it
# didn't generate. Empirically (FastAPI study bb7f3b50, 2026-05-12):
# ch01 iter 0 dropped 31 hashes; iter 4 (different model) dropped 302
# of 342 hashes — the refiner was a random walk, not a convergent loop.
#
# Fix: at chapter start, deterministically pick ONE deployment from
# SYNTH_GROUP and use it for ALL section synth + refine iters within
# that chapter. Same model sees its own previous output across iters →
# refiner converges in 2-3 iters instead of diverging over 5+.
#
# Determinism: `seed=chapter.number` → same chapter always picks the same
# deployment, even across study runs. Different chapters spread across
# pool members to balance load.
#
# Failure mode: if the pinned model has a hard outage (404, 410, auth),
# the per-call timeout cascades through num_retries=3 inside the pinned
# router. Single-model failure → chapter falls to OP-12 rescue (graceful)
# rather than cascading across the entire SYNTH_GROUP (which would
# defeat the purpose of pinning).
def pick_synth_deployment(seed: int) -> str:
    """Deterministic chapter-pin (Fix #2). seed=chapter.number.

    Round-robin across SYNTH_GROUP. Same seed → same model. Used as the
    fallback path when bandit-driven pinning is disabled or fails.
    """
    entries = _synth_entries_current()
    if not entries:
        raise RuntimeError("SYNTH_GROUP is empty — cannot pin a deployment")
    idx = seed % len(entries)
    return entries[idx]["litellm_params"]["model"]


# Per-provider chapter-pin caps (Batch 2 speed fix, 2026-05-14).
# When N concurrent chapters all hit the same provider's pool, the per-process
# asyncio semaphore in helpers.py serializes them and effective concurrency
# drops. Distributing chapter-pins ACROSS providers via Redis-backed slot
# reservations restores wall-clock parallelism. Caps match helpers._PROVIDER_
# CONCURRENCY (the per-process limit); chapter-level slot is conservative —
# fewer concurrent chapters per provider than concurrent calls.
async def pick_synth_deployment_bandit(
    seed: int,
    *,
    chapter_number: int = 0,
    expected_hash_count: int = 0,
    vault_size: int = 0,
    has_thinking_budget: bool = False,
) -> str:
    """Bandit-driven chapter-pin (Phase 2 fix, 2026-05-14).

    Replaces the static `chapter.number % N` round-robin with a per-chapter
    ParetoBandit query. The bandit's top-1 pick — informed by warm-start
    benchmark priors, accumulated production observations, AND ADWIN drift
    detection — becomes the pinned deployment for the chapter's full
    Phase A / C / refine cycle.

    This is the architectural fix for the failure mode observed in
    study 22da0586 (2026-05-14): round-robin pinning chose a model that
    was hanging on `<think>` tokens, and the bandit's per-call cascade
    couldn't help because the pinned chain had only 1 candidate. With
    bandit-driven pinning, the bandit picks the best deployment AT
    CHAPTER START, then per-call cascade still has continuity.

    On any failure (bandit cells missing, Redis unavailable, etc.), falls
    back to the deterministic round-robin via pick_synth_deployment(seed).
    """
    entries = _synth_entries_current()
    if not entries:
        raise RuntimeError("SYNTH_GROUP is empty — cannot pin a deployment")
    try:
        host = _env("REDIS_HOST", "localhost")
        port = _env("REDIS_PORT", "6379")
        password = _env("REDIS_PASSWORD")
        url = (f"redis://:{password}@{host}:{port}"
               if password else f"redis://{host}:{port}")
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
            # Phase 3 fix (2026-05-14): request top-K=5 (was 3) so the
            # provider-aware reservation pass below has more alternatives
            # to fall through to when the highest-scoring provider's slots
            # are full. Canary v7 evidence: bandit top-3 were all NIM, so
            # without provider-aware reservation, NIM saturated and the
            # cascade fell through to round-robin instead of picking from
            # a less-saturated provider.
            ranked = await bandit.predict_top_k(
                "dd-synth", 
                ctx, 
                candidates, 
                redis = rds, 
                k = 5,
            )
            # Iterate top-K and atomically reserve the first available
            # (provider_slot, deployment) pair. Provider slot is claimed
            # FIRST; if successful, deployment slot is then claimed. If
            # deployment slot fails, the provider slot is released so it
            # doesn't leak. TTL=1800s for both — matches expected chapter
            # duration; stale slots self-heal via TTL expiry.
            for deployment_id, ucb_score, n_obs in ranked:
                provider = (deployment_id.split("/", 1)[0]
                            if "/" in deployment_id else deployment_id)
                provider_cap = _PROVIDER_CHAPTER_CAPS.get(provider, 2)
                # Step 1: try to claim a provider slot.
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
                # Step 2: try to claim the deployment slot.
                reserved = await bandit.try_reserve(
                    deployment_id, 
                    "dd-synth", 
                    redis = rds, 
                    ttl_s = 1800,
                )
                if not reserved:
                    # Release the provider slot we just acquired — another
                    # chapter holds the deployment lock; we'd be double-
                    # booking otherwise.
                    await bandit.release_provider_slot(
                        provider, 
                        slot, 
                        redis = rds,
                    )
                    logger.info(
                        f"[bandit-pin] ch{chapter_number:02d} skipping "
                        f"{deployment_id} (deployment already reserved); "
                        f"trying next"
                    )
                    continue
                logger.info(
                    f"[bandit-pin] ch{chapter_number:02d} → "
                    f"{deployment_id} (ucb={ucb_score:.4f}, "
                    f"n_obs={n_obs}, reserved, "
                    f"provider_slot={provider}:{slot})"
                )
                return deployment_id
            # All top-K provider/deployment combos saturated — let
            # round-robin pick across the full pool (no slot accounting).
            logger.warning(
                f"[bandit-pin] ch{chapter_number:02d} all top-{len(ranked)} "
                f"provider/deployment slots saturated; "
                f"falling through to round-robin"
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
    # Bandit unavailable / errored / empty result — deterministic fallback.
    return pick_synth_deployment(seed)


def get_parent_group(pinned_or_parent: str | None) -> str | None:
    """Return the parent pool name (dd-synth / dd-all / dd-reduce-label) for
    a pinned-group hash, or None if the input is already a parent group or
    unknown. Caller should treat None as "input is already a parent group"
    and fall through."""
    if not pinned_or_parent:
        return None
    return _pinned_to_parent.get(pinned_or_parent)


def get_entries_for_group(group: str) -> list:
    """Return the current model entries for a parent pool name. Used by the
    bandit cascade to enumerate candidate deployments when the caller's llm
    is a pinned (single-entry) chain."""
    if group == SYNTH_GROUP:
        return _synth_entries_current()
    if group == REDUCE_LABEL_GROUP:
        return _reduce_label_entries_current()
    if group == GROUP:
        return _all_entries_current()
    return []


def build_pinned_chain_any(pinned_model: str, group: str | None = None):
    """Generalized per-call pinning (Phase 2 enhancement #3, 2026-05-14).

    Build a single-deployment ChatLiteLLMRouter for any litellm_params.model
    string, searching across the current Phase 1 catalogs. Used by
    helpers._invoke_structured_with_fallback when ParetoBandit picks a
    specific deployment for this call.

    Searches in priority: dd-synth → dd-reduce-label → dd-all (the
    dynamic-catalog steps). If `group` is given, only that group is
    searched. Falls back to None if the pinned_model isn't in any catalog
    (caller falls through to original Phase 1 chain).
    """
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
        return None    # caller decides fallback
    pinned_group = f"dd-pinned-{abs(hash(pinned_model)) & 0xFFFFFF:06x}"
    fresh_entry = {
        "model_name": pinned_group,
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
        temperature = 0.0,
    )
    _pinned_chain_cache[pinned_model] = chain
    _pinned_to_parent[pinned_group] = matched_group or GROUP
    return chain


def build_synth_pinned_chain(pinned_model: str):
    """
    Single-deployment ChatLiteLLMRouter targeting `pinned_model`.

    Built by copying the matching SYNTH_GROUP entry's litellm_params into
    a fresh single-entry Router. The original SYNTH_GROUP rotator stays
    untouched; pinned routers live in `_pinned_chain_cache` keyed by the
    pinned model string. ChatLiteLLMRouter's downstream behavior is
    unchanged (with_structured_output, ainvoke, etc.) — only the deployment
    pool size differs (1 instead of 11).

    Falls back to the full pool if `pinned_model` is not in SYNTH_GROUP
    (e.g. someone disabled it mid-run after pinning).
    """
    if pinned_model in _pinned_chain_cache:
        return _pinned_chain_cache[pinned_model]
    matching = [
        e for e in _synth_entries_current()
        if e["litellm_params"]["model"] == pinned_model
    ]
    if not matching:
        logging.getLogger(__name__).warning(
            f"[synth-pin] {pinned_model!r} not in SYNTH_GROUP; "
            f"falling back to full pool"
        )
        return build_synth_pool_chain()
    pinned_group = f"dd-synth-pinned-{abs(hash(pinned_model)) & 0xFFFFFF:06x}"
    fresh_entry = {
        "model_name": pinned_group,
        "litellm_params": dict(matching[0]["litellm_params"]),
    }
    pinned_router = Router(
        model_list = [fresh_entry],
        routing_strategy = "simple-shuffle",
        enable_pre_call_checks = True,
        num_retries = 3,            # tighter — only one deployment
        cooldown_time = 30,         # shorter; single-deployment can't waste
        set_verbose = False,
    )
    chain = ChatLiteLLMRouter(
        router = pinned_router, 
        model = pinned_group, 
        temperature = 0.0,
    )
    _pinned_chain_cache[pinned_model] = chain
    _pinned_to_parent[pinned_group] = SYNTH_GROUP
    return chain


def build_refine_llm_chain(
    groq_timeout_s: int = 120,
    nim_timeout_s: int = 300):
    """Self-Refine refiner at T=0.7 (Madaan 2023 §2). Unified catalog."""
    return ChatLiteLLMRouter(
        router = _get_router(), 
        model = GROUP, 
        temperature = 0.7)


def build_curator_llm(timeout_s: int = 600):
    """
    Curator chain. Uses the same unified catalog at T=0.0 — previous
    single-model pin per Mixture-of-Agents (arXiv 2406.04692) is relaxed
    per design decision 2026-04-24: unified rotation for every step.
    """
    return ChatLiteLLMRouter(
        router = _get_router(), 
        model = GROUP, 
        temperature = 0.0)


def build_keylm_chain():
    """
    Tiny-LM chain for the classical MAP step's cluster-label generation
    (KeyLLM-style). Routes to KEYLM_GROUP — currently NIM Llama-3.2-1B
    primary, Groq Llama-3.2-1B-preview fallback. T=0.0 for deterministic
    output; max_tokens applied per-call by the caller (typically 16 — a
    2-4 word Title-Case title fits in 8-12 BPE tokens with margin).

    See docs/KD-PLANNER-MAP-OPTIMIZATION.md §5 for the model selection
    rationale. KeyLLM is intentionally NOT routed through the dd-all
    frontier rotator — small task, small model.
    """
    return ChatLiteLLMRouter(
        router = _get_router(), 
        model = KEYLM_GROUP, 
        temperature = 0.0)


def build_reduce_label_chain():
    """
    Dedicated non-reasoning rotator for the Clio REDUCE step's per-meta-cluster
    labeling (`_label_one`) and chapter ordering (`order_chain`) calls. Routes
    to REDUCE_LABEL_GROUP — a curated 8-deployment pool of fast small/medium
    models with native function-calling or json-schema response_format support.

    T=1.0 (Polish #5b, 2026-05-11). Gemini-3 explicitly requires T=1.0 (per
    Google's API, T<1.0 "can cause infinite loops, degraded reasoning
    performance, and failure on complex tasks"). The factory's call-time T
    overrides any per-deployment litellm_params.temperature in LiteLLM Router,
    so setting T per-deployment doesn't work — we move it here. With R2's
    json_schema mode, output structure is enforced server-side regardless of
    T, so non-Gemini deployments (Groq, NIM, Mistral) running at T=1.0
    produce equally valid output, only sampling among valid JSON paths varies.

    See R1/R2 in docs/KD-PLANNER-REDUCE-MAY2026-OPTIMIZATION.md for the
    design rationale — this group exists to keep REDUCE labeling off the
    dd-all reasoning models that burn the 300s NIM gateway budget on <think>
    blocks for what is structurally a 3K-token classification task.
    """
    return ChatLiteLLMRouter(
        router = _get_router(), 
        model = REDUCE_LABEL_GROUP, 
        temperature = 1.0,
    )


# =============================================================================
# DYNAMIC CATALOG — discovery + benchmarks → top-K per step (Phase 1, 2026-05-14)
# =============================================================================
# When DD_DYNAMIC_CATALOG=1 (default) and init_dynamic_catalog() succeeds at
# startup, the dd-all / dd-synth / dd-reduce-label groups are populated from:
#
#    domains.llm.discovery.list_all_alive_models()       (live free-tier models)
#                          ↓
#    domains.llm.benchmarks.rank_for_step(step, alive)   (composite scoring)
#                          ↓
#    top-K cut per step  → _record_to_entry() → LiteLLM model_list dict
#
# Falls back to the static catalog (_all_entries / _synth_entries /
# _reduce_label_entries) on any failure. dd-keylm and dd-embed stay static —
# dd-keylm needs tiny instruct LMs (≤3B params, no benchmark coverage), and
# dd-embed is single-entry by cosine-geometry design.
#
# pick_synth_deployment() and build_synth_pinned_chain() both read from
# _synth_entries_current() so per-chapter pinning stays consistent with the
# Router's model_list (no orphan deployments).
#
# Cold-start sequence:
#   FastAPI lifespan       → await init_dynamic_catalog() BEFORE build_*chain()
#   Celery worker_process_init → init_dynamic_catalog_sync() at fork
#   On failure              → static catalog, log warning, continue
#
# Disable via env: DD_DYNAMIC_CATALOG=0
# =============================================================================
def _record_to_entry(group: str, record, timeout_s: int) -> dict | None:
    """Convert a discovery.DiscoveryRecord → LiteLLM model_list entry.

    Dispatches by provider to the existing `_xxx_entry()` helpers so the
    resulting dict shape is byte-identical to the static catalog. Returns
    None for unsupported providers (SambaNova/DeepSeek-direct held disabled
    in services/discovery.py).
    """
    p, m = record.provider, record.model_id
    if not m:
        return None
    if p == "groq":      return _groq_entry(group, m, timeout_s = timeout_s)
    if p == "nim":       return _nim_entry(group, m, timeout_s = timeout_s)
    if p == "cerebras":  return _cerebras_entry(group, m, timeout_s = timeout_s)
    if p == "mistral":   return _mistral_entry(group, m, timeout_s = timeout_s)
    if p == "gemini":    return _gemini_entry(group, m, timeout_s = timeout_s)
    return None


def _all_entries_current() -> list:
    """Active catalog for dd-all — dynamic if available, else static fallback."""
    if _dynamic_catalog_initialized and _dynamic_entries.get("dd-all"):
        return _dynamic_entries["dd-all"]
    return _all_entries()


def _synth_entries_current() -> list:
    """Active catalog for dd-synth — dynamic if available, else static fallback."""
    if _dynamic_catalog_initialized and _dynamic_entries.get("dd-synth"):
        return _dynamic_entries["dd-synth"]
    return _synth_entries()


def _reduce_label_entries_current() -> list:
    """Active catalog for dd-reduce-label — dynamic if available, else static."""
    if _dynamic_catalog_initialized and _dynamic_entries.get("dd-reduce-label"):
        return _dynamic_entries["dd-reduce-label"]
    return _reduce_label_entries()


def _build_redis_url_for_bench() -> str | None:
    """Construct Redis URL from env vars for benchmark cache (90d TTL)."""
    host = _env("REDIS_HOST")
    if not host:
        return None
    port = _env("REDIS_PORT", "6379")
    password = _env("REDIS_PASSWORD")
    if password:
        return f"redis://:{password}@{host}:{port}"
    return f"redis://{host}:{port}"


async def init_dynamic_catalog() -> bool:
    """Populate _dynamic_entries from live discovery + benchmark ranking.

    Idempotent — re-calling is a no-op once initialized successfully.
    Call from FastAPI lifespan startup BEFORE the first build_*_chain().
    Returns True on success (dynamic catalog active), False if it fell back
    to the static catalog.

    Behavior:
      - DD_DYNAMIC_CATALOG=0 → skip entirely, use static.
      - Discovery returns 0 models → fall back, log warning.
      - Benchmark fetch raises → fall back, log warning.
      - Per-step top-K is empty (all unscored AND no static fallback) → still
        materialize Router with whatever the static catalog returns.
    """
    global _dynamic_catalog_initialized, _dynamic_entries
    if _dynamic_catalog_initialized:
        return True
    flag = os.environ.get("DD_DYNAMIC_CATALOG", "1").strip().lower()
    if flag not in ("1", "true", "yes", "on"):
        logger.info("[llm-chain] DD_DYNAMIC_CATALOG=0 — using static catalog")
        return False
    try:
        redis_url = _build_redis_url_for_bench()
        rds = redis_aio.from_url(redis_url) if redis_url else None
        try:
            by_provider = await discovery.list_all_alive_models()
            alive = [r for records in by_provider.values() for r in records]
            if not alive:
                raise RuntimeError("discovery returned 0 alive models")
            logger.info(
                f"[llm-chain] dynamic catalog: discovery returned "
                f"{len(alive)} models across {len(by_provider)} providers"
            )
            for step, top_k in _DYNAMIC_TOP_K.items():
                group_name = _DYNAMIC_STEP_TO_GROUP[step]
                timeout_s = _DYNAMIC_STEP_TIMEOUT_S[step]
                try:
                    ranked = await benchmarks.rank_for_step(
                        step, 
                        alive, 
                        redis = rds)
                except Exception as e:
                    logger.warning(
                        f"[llm-chain] rank_for_step({step}) failed: "
                        f"{type(e).__name__}: {e}; using static for this step"
                    )
                    continue
                # Take top-K with composite_score > 0 first, then fill with
                # unscored top-tier-fallback entries until we hit top_k or
                # exhaust the ranked list.
                scored_top: list = []
                unscored_top: list = []
                for record, score in ranked:
                    if score > 0:
                        scored_top.append(record)
                    else:
                        unscored_top.append(record)
                pool_records = scored_top[:top_k]
                # Backfill with unscored (provider-tier-sorted) if we have
                # room — keeps the pool deep enough for cooldown redundancy.
                if len(pool_records) < top_k:
                    pool_records.extend(unscored_top[: top_k - len(pool_records)])
                entries: list[dict] = []
                seen_litellm_models: set[str] = set()
                for r in pool_records:
                    entry = _record_to_entry(group_name, r, timeout_s)
                    if entry is None:
                        continue
                    # Dedupe by litellm_params.model so we don't include the
                    # same (provider, model_id) twice in one pool.
                    key = entry["litellm_params"]["model"]
                    if key in seen_litellm_models:
                        continue
                    seen_litellm_models.add(key)
                    entries.append(entry)
                if entries:
                    _dynamic_entries[step] = entries
                    logger.info(
                        f"[llm-chain] dynamic catalog: {step} → "
                        f"{len(entries)} entries (top-K={top_k})"
                    )
                else:
                    logger.warning(
                        f"[llm-chain] dynamic catalog: {step} produced 0 "
                        f"entries; static fallback for this step"
                    )
        finally:
            if rds:
                try:
                    await rds.aclose()
                except Exception:
                    pass
        # Mark initialized if at least one step got dynamic entries
        if _dynamic_entries:
            _dynamic_catalog_initialized = True
            logger.info(
                f"[llm-chain] dynamic catalog ACTIVE for: "
                f"{sorted(_dynamic_entries.keys())}"
            )
            return True
        logger.warning("[llm-chain] dynamic catalog: 0 steps populated; full static fallback")
        return False

    except Exception as e:
        logger.warning(
            f"[llm-chain] dynamic catalog init failed: "
            f"{type(e).__name__}: {e}; using static catalog"
        )
        _dynamic_entries.clear()
        _dynamic_catalog_initialized = False
        return False


def init_dynamic_catalog_sync() -> bool:
    """Sync wrapper for non-async callers (Celery worker_process_init).

    Spins up a private event loop. Do NOT call from inside an existing loop.
    """

    try:
        return asyncio.run(init_dynamic_catalog())
    except Exception as e:
        logger.warning(
            f"[llm-chain] init_dynamic_catalog_sync failed: "
            f"{type(e).__name__}: {e}"
        )
        return False