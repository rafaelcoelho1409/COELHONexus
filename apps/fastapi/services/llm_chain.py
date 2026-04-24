"""
Unified LLM Router — LiteLLM-backed with fail-fast pre-call checks.

DESIGN 2026-04-24: ONE ranked catalog (`kd-all`) reused by every KD step.
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
  DeepSeek=2 | Zhipu=2    →   37 entries total, 8 providers

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

Factories (all serve from the same `kd-all` group, varying only temperature):
  - build_llm_fallback_chain          — T=0.0
  - build_resolver_llm_chain           — T=0.0
  - build_synth_fallback_chain         — T=0.0 (synthesize_chapter, curator)
  - build_refine_llm_chain             — T=0.7 (Self-Refine per Madaan 2023)
  - build_curator_llm                  — T=0.0
  - build_scope_classifier_llm         — T=0.0
"""
import os
from langchain_litellm.chat_models import ChatLiteLLMRouter
from litellm import Router
from litellm.types.router import (
    RetryPolicy,
    AllowedFailsPolicy,
    ModelGroupInfo,
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


def _zhipu_entry(group: str, model: str, timeout_s: int = 120) -> dict:
    # Zhipu is OpenAI-compatible; we set api_base explicitly since LiteLLM
    # doesn't have a dedicated `zhipu/` prefix (uses OpenAI-compat path).
    return {
        "model_name": group,
        "litellm_params": {
            "model": f"openai/{model}",
            "api_key": _env("ZHIPU_API_KEY"),
            "api_base": "https://open.bigmodel.cn/api/paas/v4",
            "timeout": timeout_s,
            "max_retries": 0,
        },
    }


# =============================================================================
# Unified ranked catalog — `kd-all`
# =============================================================================
# SAME list served to every KD step. Ordering is strictly best→worst by
# 2026-04-24 benchmark data (SWE-Bench Verified, MMLU-Pro, AIME-2025,
# LMArena Elo, AAII, LiveCodeBench, GPQA, HumanEval — composite).
# Providers interleaved so no 3 in a row of same provider anywhere.
GROUP = "kd-all"


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
        _nim_entry(GROUP, "moonshotai/kimi-k2-thinking", timeout_s=300),                           # AAII 67 — highest on list; HLE 44.9%, 200-300 tool-call coherence, 256K ctx
        # _deepseek_entry(GROUP, "deepseek-v4-pro", timeout_s=300),                                # DISABLED 2026-04-24 — "Insufficient Balance" on account. Re-enable after top-up (5M free grant used up or V4 not in free tier). AAII ~57, 1T+ MoE FP4, 1M ctx
        _nim_entry(GROUP, "z-ai/glm-5.1", timeout_s=300),                                          # AAII 51 (Reasoning); SWE-Bench Pro 58.4% (#1 OSS); may be skipped during NIM endpoint flakiness
        _nim_entry(GROUP, "minimaxai/minimax-m2.7", timeout_s=300),                                # AAII 50 — 204K ctx agentic, SWE-Pro 56.22%, SWE-Multilingual 76.5
        # _groq_entry(GROUP, "moonshotai/kimi-k2-instruct", timeout_s=120),                        # DISABLED 2026-04-24 — not in Groq's actual catalog (research agent hallucinated; Groq listing confirmed missing). AAII 49 (K2-0905), 256K ctx
        # _deepseek_entry(GROUP, "deepseek-v4-flash", timeout_s=180),                              # DISABLED 2026-04-24 — "Insufficient Balance" (same DeepSeek account as v4-pro). AAII 47 (Max), 284B MoE, 1M ctx
        _nim_entry(GROUP, "moonshotai/kimi-k2.5", timeout_s=300),                                  # AAII 47 (R) / 37 (NR) — Arena Elo ~1447, 262K ctx, powers Cursor Composer 2
        _gemini_entry(GROUP, "gemini-3-flash-preview", timeout_s=120),                             # AAII 46 (R) / 35 (NR) — LiveCodeBench 90.8%, SWE-bench 78%, 1M ctx
        _nim_entry(GROUP, "qwen/qwen3.5-397b-a17b", timeout_s=300),                                # AAII 45 (R) / 40 (NR) — MMLU-Pro 87.18%, 262K ctx
        _nim_entry(GROUP, "deepseek-ai/deepseek-v3.2", timeout_s=300),                             # AAII 42 (R) / 32 (NR) — SWE-Bench 72-74%, IMO gold, 163K ctx
        # --- 11–17: Strong second tier (AAII 34–42) ---
        # _sambanova_entry(GROUP, "MiniMax-M2.5", timeout_s=180),                                  # DISABLED 2026-04-24 — SambaNova response: "A payment method is required". Re-enable after adding payment method. AAII 42, SWE-Bench 80.2% (highest SWE), 160K ctx
        _nim_entry(GROUP, "minimaxai/minimax-m2.5", timeout_s=300),                                # AAII 42 — DUP of #11 (same model, NIM infra)
        # _cerebras_entry(GROUP, "zai-glm-4.7", timeout_s=120),                                    # DISABLED 2026-04-24 — 404 "you do not have access to it" (model exists in Cerebras catalog but API key lacks access). AAII 42 (R) / 34 (NR), SOTA τ²-Bench, 200K ctx, 355B params
        _nim_entry(GROUP, "nvidia/nemotron-3-super-120b-a12b", timeout_s=300),                     # AAII 36 — 1M ctx, hybrid Mamba, leads size class on AIME-2025 + Terminal-Bench
        _sambanova_entry(GROUP, "DeepSeek-V3.1", timeout_s=180),                                   # AAII 34 (R) — AIME 93.1%, hybrid reasoning, 128K ctx
        _nim_entry(GROUP, "deepseek-ai/deepseek-v3.1-terminus", timeout_s=300),                    # AAII 34 (R) / 29 (NR) — V3.1-Terminus, slightly newer than V3.1, 128K ctx
        _gemini_entry(GROUP, "gemini-3.1-flash-lite-preview", timeout_s=90),                       # AAII 34 — GPQA Diamond 86.9%, 381 t/s, 1M ctx
        # --- 18–21: gpt-oss-120b on four providers (AAII 33 each) ---
        # _cerebras_entry(GROUP, "gpt-oss-120b", timeout_s=120),                                   # DISABLED 2026-04-24 — 404 "you do not have access to it" (model listed in Cerebras catalog but key unauthorized). AAII 33, MMLU-Pro 90.0%, 3000 tok/s
        # _sambanova_entry(GROUP, "gpt-oss-120b", timeout_s=180),                                  # DISABLED 2026-04-24 — SambaNova response: "A payment method is required". Same model as #18 family
        _groq_entry(GROUP, "openai/gpt-oss-120b", timeout_s=120),                                  # AAII 33 — DUP family; confirmed working on Groq
        _nim_entry(GROUP, "openai/gpt-oss-120b", timeout_s=180),                                   # AAII 33 — DUP family; confirmed working on NIM
        # --- 22–23: AAII 30 ---
        _zhipu_entry(GROUP, "glm-4.7-flash", timeout_s=120),                                       # AAII 30 (R) — 30B-A3B MoE, best-in-30B-class, AIME-2025 95.7%, 200K ctx, zero-cap free
        _gemini_entry(GROUP, "gemini-2.5-flash", timeout_s=120),                                   # AAII ~30 — GPQA 82.8, MMLU-Lite 88.4, AIME 88, 1M ctx (stable prod)
        # --- 24–28: AAII 22–28 (Mistral cluster + glm-4.5-flash) ---
        _mistral_entry(GROUP, "mistral-small-latest", timeout_s=120),                              # AAII 28 — Mistral Small 4 v26.03, HumanEval 92, MMLU 88.5 (surprisingly > Medium 3.1)
        _mistral_entry(GROUP, "magistral-medium-latest", timeout_s=180),                           # AAII 27 — Magistral 1.2, AIME24 91.82%, GPQA-Diamond 76.3% (reasoning specialist)
        _mistral_entry(GROUP, "mistral-large-latest", timeout_s=180),                              # AAII 23 — Mistral Large 3 v25.12, LMArena #2 OSS, MATH-500 93.6, 256K ctx
        _nim_entry(GROUP, "mistralai/mistral-large-3-675b-instruct-2512", timeout_s=300),          # AAII 23 — DUP of #26 (same Large 3 model, NIM infra)
        _zhipu_entry(GROUP, "glm-4.5-flash", timeout_s=120),                                       # AAII ~23 — GLM-4.5 tier (legacy), zero-cap free fallback
        # --- 29–31: AAII 21–22 ---
        _mistral_entry(GROUP, "devstral-medium-latest", timeout_s=180),                            # AAII 22 — Devstral 2 code-agents, SWE-Bench Verified 46.8%, 256K ctx
        _gemini_entry(GROUP, "gemini-2.5-flash-lite", timeout_s=90),                               # AAII 22 (R) / 19 (NR) — high-throughput fallback, 1000 RPD free
        _mistral_entry(GROUP, "mistral-medium-latest", timeout_s=120),                             # AAII 21 — Mistral Medium 3.1 v25.08, Arena top-10 overall
        # --- 32–37: Tail (AAII ≤20) ---
        _groq_entry(GROUP, "qwen/qwen3-32b", timeout_s=120),                                       # AAII ~20 — Pre-3.5 generation, LiveCodeBench 54.4, thinking-mode
        _mistral_entry(GROUP, "magistral-small-latest", timeout_s=180),                            # AAII 18 — Magistral Small 1.2, 24B reasoner
        _sambanova_entry(GROUP, "Llama-4-Maverick-17B-128E-Instruct", timeout_s=180),              # AAII 18 — 400B MoE/17B active, 128K ctx (Preview)
        _nim_entry(GROUP, "meta/llama-4-maverick-17b-128e-instruct", timeout_s=300),               # AAII 18 — DUP of #34 (same Maverick, NIM infra, 1M ctx)
        _groq_entry(GROUP, "meta-llama/llama-4-scout-17b-16e-instruct", timeout_s=120),            # AAII ~15 — 10M native ctx but smaller/weaker Llama 4 variant
        # _sambanova_entry(GROUP, "Meta-Llama-3.3-70B-Instruct", timeout_s=180),                   # DISABLED 2026-04-24 — SambaNova response: "A payment method is required". AAII 14, 128K ctx on SambaNova

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
        # Zhipu paid / non-text
        #   - zhipu/glm-4-32b-0414-128k ($0.10/M, not truly free)
        #   - zhipu/glm-4.7-flashx (paid)
        #   - zhipu/glm-5 / 5.1 / 5-Turbo / 4.7 / 4.6 / AirX (all paid)
        #   - zhipu/glm-z1-flash (not a Z.AI API SKU — open-weights only on HF/Ollama)
        #   - zhipu/glm-4.6v-flash (vision-only)
    ]


# =============================================================================
# Unified Router — single instance shared across all factories
# =============================================================================
_router_instance: Router | None = None


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
        AuthenticationErrorRetries=CASCADE_DEPTH,
        ContentPolicyViolationErrorRetries=CASCADE_DEPTH,
        RateLimitErrorRetries=CASCADE_DEPTH,
        BadRequestErrorRetries=CASCADE_DEPTH,
        TimeoutErrorRetries=CASCADE_DEPTH,
        InternalServerErrorRetries=CASCADE_DEPTH,
    )

    allowed_fails_policy = AllowedFailsPolicy(
        AuthenticationErrorAllowedFails=0,    # invalid key = cooldown immediately
        BadRequestErrorAllowedFails=1,        # 400/404/413 on first call = stop trying this model
        ContentPolicyViolationErrorAllowedFails=2,
        RateLimitErrorAllowedFails=1,         # 429 = cooldown immediately
        TimeoutErrorAllowedFails=2,           # 2 timeouts → cooldown
        InternalServerErrorAllowedFails=2,    # 5xx same
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
        model_list=_all_entries(),
        # simple-shuffle is recommended for production (LiteLLM docs): doesn't
        # add Redis round-trips per request the way usage-based-routing does.
        # Combined with allowed_fails_policy this effectively routes among
        # HEALTHY entries in priority order.
        routing_strategy="simple-shuffle",
        enable_pre_call_checks=True,         # fail-fast core — skip cooled-down at 0ms
        allowed_fails=3,                      # generic threshold (per-error policy overrides)
        allowed_fails_policy=allowed_fails_policy,
        cooldown_time=60,                     # TTL for auto-recovery
        retry_policy=retry_policy,
        num_retries=CASCADE_DEPTH,            # cascade length — on failure, try another deployment up to 40 times
        set_verbose=False,
        **redis_kwargs,
    )
    return _router_instance


# =============================================================================
# Public factory API — all serve from the same `kd-all` group
# =============================================================================
# Temperature is the only per-step variation. T=0.7 for Self-Refine
# exploration (Madaan 2023 §2); T=0.0 for deterministic calls elsewhere.
# Per-entry timeouts in the catalog reflect provider characteristics;
# the factory-level timeout args are kept for API compatibility only.

def build_llm_fallback_chain(
    groq_timeout_s: int = 120,
    nim_timeout_s: int = 300):
    """General-purpose LLM chain. Unified catalog at T=0.0."""
    return ChatLiteLLMRouter(router=_get_router(), model=GROUP, temperature=0.0)


def build_resolver_llm_chain(
    groq_timeout_s: int = 30,
    nim_timeout_s: int = 60):
    """Resolver chain. Unified catalog at T=0.0."""
    return ChatLiteLLMRouter(router=_get_router(), model=GROUP, temperature=0.0)


def build_synth_fallback_chain(
    groq_timeout_s: int = 120,
    nim_timeout_s: int = 300):
    """Synthesize_chapter + curator. Unified catalog at T=0.0."""
    return ChatLiteLLMRouter(router=_get_router(), model=GROUP, temperature=0.0)


def build_refine_llm_chain(
    groq_timeout_s: int = 120,
    nim_timeout_s: int = 300):
    """Self-Refine refiner at T=0.7 (Madaan 2023 §2). Unified catalog."""
    return ChatLiteLLMRouter(router=_get_router(), model=GROUP, temperature=0.7)


def build_curator_llm(timeout_s: int = 600):
    """
    Curator chain. Uses the same unified catalog at T=0.0 — previous
    single-model pin per Mixture-of-Agents (arXiv 2406.04692) is relaxed
    per design decision 2026-04-24: unified rotation for every step.
    """
    return ChatLiteLLMRouter(router=_get_router(), model=GROUP, temperature=0.0)


def build_scope_classifier_llm(timeout_s: int = 30):
    """
    Scope classifier for POST /studies. Unified catalog at T=0.0 — previous
    tiny-model tail (llama-3.1-8b, gpt-oss-20b) is relaxed per design
    decision 2026-04-24: quality > speed, tokens are free.
    """
    return ChatLiteLLMRouter(router=_get_router(), model=GROUP, temperature=0.0)
