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
"""
import logging
import os
from langchain_litellm.chat_models import ChatLiteLLMRouter
from litellm import Router

logger = logging.getLogger(__name__)
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

# =============================================================================
# Small-LM group — `kd-keylm` (KeyLLM cluster labeling for the classical MAP)
# =============================================================================
# Tiny instruct LMs (≤1B) for short-output format-strict tasks like the
# 2-4 word cluster titles emitted by graphs/knowledge/classical_map.py's
# KeyLLM step. NOT for synthesis — those go to GROUP=kd-all.
#
# Why a separate group: the kd-all rotator's frontier 70B+ models would
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
KEYLM_GROUP = "kd-keylm"


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
        _nim_entry(KEYLM_GROUP, "meta/llama-3.2-1b-instruct", timeout_s=30),
        # Fallback: 3B when 1B is cooled down (rate-limit absorber)
        _nim_entry(KEYLM_GROUP, "meta/llama-3.2-3b-instruct", timeout_s=45),
    ]


# =============================================================================
# REDUCE labeling group — `kd-reduce-label` (Clio per-meta naming + ordering)
# =============================================================================
# Curated pool for the Clio REDUCE step's `_label_one` parallel calls and the
# single `order_chain` call (apps/fastapi/graphs/knowledge/reduce_cluster.py).
# Each call is ~3K tokens, structurally a classification task ("name this
# group of 30 related topics"). Co-mingling these with kd-all's reasoning
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
# Specifically EXCLUDED-by-design vs kd-all:
#   - SambaNova (whole provider paywalled per 2026-04-24 Run-8)
#   - Cerebras gpt-oss-120b (this account 404; user's key lacks model access)
#   - DeepSeek V4 (Insufficient Balance per kd-all comments)
#   - Zhipu glm-*-flash (auth/endpoint failures, 0/14 success in Run-16)
#   - Groq gpt-oss-120b (8K TPM ceiling) and qwen3-32b (6K TPM) — also
#     excluded from kd-all for the same reason
#   - All reasoning-mode models even when reachable (the whole point)
#
# Pool sized for M=8-12 parallel labeling fanout. With 8 deployments and
# per-deployment cooldown, a single bad provider takes out ≤1 entry.
REDUCE_LABEL_GROUP = "kd-reduce-label"


def _reduce_label_entries() -> list:
    """
    Non-reasoning rotator for the REDUCE step's labeling + ordering calls.

    Order: fastest LPU/TPU silicon first (Groq, Gemini Flash-Lite), then
    NIM hybrid-Mamba + gpt-oss + Mistral-Large-3, then Mistral direct,
    then Llama-4 Maverick as deep tail. ~8 deployments — generous
    cooldown-redundancy for parallel labeling.

    Timeouts are tighter than kd-all (60-90s vs 120s) — these calls are
    structurally short; a long delay almost always means a flaky model
    head and we'd rather fall through faster than wait it out.
    """
    return [
        # --- Tier 1: LPU/TPU silicon, sub-100ms TTFT, native tools ---
        # Groq llama-3.3-70b-versatile is EXCLUDED from kd-all only because of
        # the code-gen error benchmark; chapter naming doesn't generate code,
        # so that exclusion doesn't apply here. 12K TPM ample for ~3K prompts.
        _groq_entry(REDUCE_LABEL_GROUP, "llama-3.3-70b-versatile", timeout_s=60),
        # Gemini 3.1 Flash-Lite preview: 381 t/s, AAII 34, 1M ctx, native tools.
        # Distinct from `gemini-2.5-flash-lite` which is disabled in kd-all
        # (returns empty choices on the complex ChapterOutput schema) — the
        # REDUCE schemas (MetaLabelDraft / OrderedIndices) are much simpler.
        # Gemini-3 requires T=1.0 (Google's API: "Setting temperature < 1.0
        # for Gemini 3 models can cause infinite loops, degraded reasoning
        # performance, and failure on complex tasks"). Polish #5b (2026-05-11):
        # the factory `build_reduce_label_chain` now passes T=1.0 to ALL
        # kd-reduce-label deployments — call-time temperature wins over
        # deployment-level litellm_params in LiteLLM Router, so the per-
        # deployment override approach we tried first didn't take effect.
        # json_schema mode keeps output valid at T=1.0 for non-Gemini
        # deployments too; only sampling among valid JSON paths differs.
        _gemini_entry(REDUCE_LABEL_GROUP, "gemini-3.1-flash-lite-preview", timeout_s=60),
        # --- Tier 2: NIM-hosted, non-reasoning, high-context ---
        # Nemotron-3-super-120b-a12b: 1M ctx hybrid Mamba, AAII 36, leads
        # size class on AIME-2025 + Terminal-Bench. Non-reasoning by default
        # (no detailed_thinking parameter exposed by the NIM endpoint).
        _nim_entry(REDUCE_LABEL_GROUP, "nvidia/nemotron-3-super-120b-a12b", timeout_s=90),
        # gpt-oss-120b on NIM — Groq's 8K TPM ceiling makes Groq's host
        # permanently incompatible; Cerebras 404s on this account; NIM is
        # the only viable host for the gpt-oss family on this account.
        _nim_entry(REDUCE_LABEL_GROUP, "openai/gpt-oss-120b", timeout_s=90),
        # Mistral Large 3 via NIM (DUP of Mistral direct below, NIM infra
        # adds an independent failure domain).
        _nim_entry(REDUCE_LABEL_GROUP, "mistralai/mistral-large-3-675b-instruct-2512", timeout_s=90),
        # --- Tier 3: Mistral direct API ---
        # Mistral Large 3 v25.12 — LMArena #2 OSS, native function calling,
        # 256K ctx. Same model as the NIM entry above; different infra.
        _mistral_entry(REDUCE_LABEL_GROUP, "mistral-large-latest", timeout_s=90),
        # Mistral Small 4 v26.03 — HumanEval 92, MMLU 88.5, AAII 28 — outperforms
        # Mistral Medium 3.1 on most benchmarks; fastest viable fallback here.
        _mistral_entry(REDUCE_LABEL_GROUP, "mistral-small-latest", timeout_s=60),
        # --- Tier 4: deep tail ---
        # Llama-4 Maverick 17B-128E MoE on NIM — 1M ctx, AAII 18; weak relative
        # to the head of the pool but absorbs cooldown bursts when everything
        # above is in cooldown. Same-token-budget cost as the tier-1 entries.
        _nim_entry(REDUCE_LABEL_GROUP, "meta/llama-4-maverick-17b-128e-instruct", timeout_s=90),
    ]


# =============================================================================
# Embedding group — `kd-embed` (vector embeddings for KD MAP/REDUCE/preview)
# =============================================================================
# **SINGLE-ENTRY by design** — embedding rotation across providers breaks
# cosine geometry within a study (different model = different vector space).
# If NIM is down for an extended period, the LiteLLM Router's per-deployment
# cooldown + retry policy handles transient failures (5xx/429/timeout) on
# the same deployment; longer outages = study fails, user retries (cheap).
# Don't add a second model to this group — see project_planner_map_replacement
# memory for the regression that motivated this rule.
#
# Pick rationale (research-validated 2026-05-09 night):
# - 40 RPM, NO monthly cap (Mistral's 2 RPM too tight for bulk filter)
# - Commercial license OK (Jina v4 is non-commercial — license trap)
# - Same NVIDIA_API_KEY already in use for the LLM rotator
# - Higher MTEB rank than `bge-m3` / `e5-mistral` at sub-2B size class
# - 2048-dim is plenty (KD's REDUCE PCA-reduces to 128 anyway)
KD_EMBED_GROUP = "kd-embed"

# Hard upper bound on inputs per /v1/embeddings call. NIM doesn't publish a
# strict limit; 64 is empirically safe and matches the previous Xinference
# batch tuning. Helper functions auto-batch above this.
KD_EMBED_BATCH_SIZE = 64


def _embed_entries() -> list:
    """
    Single-entry embedding group — see KD_EMBED_GROUP docstring above.

    Two NIM-specific params are baked into litellm_params so they apply on
    every call (LiteLLM Router strips unknown call-time kwargs):

      - `encoding_format="float"` — required by NIM's OpenAI-compat /v1/embeddings.
        Without it, NIM 400s with: "Input should be 'float' or 'base64'".

      - `input_type="passage"` — required for ASYMMETRIC embedding models
        (NV-Embed family has different heads for queries vs passages).
        Use "passage" because KD's clustering compares document-vs-document
        similarity, not query-vs-document. Without it, NIM 400s with:
        "'input_type' parameter is required for asymmetric models".
    """
    return [
        {
            "model_name": KD_EMBED_GROUP,
            "litellm_params": {
                "model":           "nvidia_nim/nvidia/llama-nemotron-embed-1b-v2",
                "api_key":         _env("NVIDIA_API_KEY"),
                "timeout":         120,
                "max_retries":     0,
                "encoding_format": "float",
                "input_type":      "passage",
            },
        },
    ]


def embed_via_router_sync(texts: list[str]) -> list[list[float]]:
    """
    Sync batch-embed via the rotator's `kd-embed` group. Auto-batches at
    KD_EMBED_BATCH_SIZE; returns vectors in input order. Empty/whitespace
    inputs are substituted with " " to keep the OpenAI-style /v1/embeddings
    call happy (a real provider would 400 on empty inputs).

    Returns flat list of vectors. Caller is responsible for tuple-wrapping
    with a provider label (see services.knowledge.embeddings.embed_texts_sync).
    """
    if not texts:
        return []
    router = _get_router()
    clean = [t if (t and t.strip()) else " " for t in texts]
    out: list[list[float]] = []
    for start in range(0, len(clean), KD_EMBED_BATCH_SIZE):
        batch = clean[start:start + KD_EMBED_BATCH_SIZE]
        # NIM's `nvidia/llama-nemotron-embed-1b-v2` is asymmetric (separate
        # query/passage encoding heads). Both kwargs are required; verified
        # 2026-05-09 with direct litellm.embedding(...) call. `extra_body=`
        # does NOT work for LiteLLM embeddings (gets passed as a literal
        # field, NIM rejects it).
        response = router.embedding(
            model=KD_EMBED_GROUP,
            input=batch,
            encoding_format="float",
            input_type="passage",
        )
        # LiteLLM normalizes to OpenAI shape: response["data"] is a list of
        # {"embedding": [...], "index": N, "object": "embedding"}.
        out.extend(item["embedding"] for item in response["data"])
    if len(out) != len(texts):
        raise RuntimeError(
            f"kd-embed: rotator returned {len(out)} vectors for {len(texts)} inputs"
        )
    return out


async def embed_via_router_async(texts: list[str]) -> list[list[float]]:
    """Async equivalent of embed_via_router_sync."""
    if not texts:
        return []
    router = _get_router()
    clean = [t if (t and t.strip()) else " " for t in texts]
    out: list[list[float]] = []
    for start in range(0, len(clean), KD_EMBED_BATCH_SIZE):
        batch = clean[start:start + KD_EMBED_BATCH_SIZE]
        # encoding_format + input_type both required — see embed_via_router_sync.
        response = await router.aembedding(
            model=KD_EMBED_GROUP,
            input=batch,
            encoding_format="float",
            input_type="passage",
        )
        out.extend(item["embedding"] for item in response["data"])
    if len(out) != len(texts):
        raise RuntimeError(
            f"kd-embed: rotator returned {len(out)} vectors for {len(texts)} inputs"
        )
    return out


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
        _nim_entry(GROUP, "moonshotai/kimi-k2-thinking", timeout_s=120),                           # AAII 67 — highest on list; HLE 44.9%, 200-300 tool-call coherence, 256K ctx
        # _deepseek_entry(GROUP, "deepseek-v4-pro", timeout_s=120),                                # DISABLED 2026-04-24 — "Insufficient Balance" on account. Re-enable after top-up (5M free grant used up or V4 not in free tier). AAII ~57, 1T+ MoE FP4, 1M ctx
        _nim_entry(GROUP, "z-ai/glm-5.1", timeout_s=120),                                          # AAII 51 (Reasoning); SWE-Bench Pro 58.4% (#1 OSS); may be skipped during NIM endpoint flakiness
        _nim_entry(GROUP, "minimaxai/minimax-m2.7", timeout_s=120),                                # AAII 50 — 204K ctx agentic, SWE-Pro 56.22%, SWE-Multilingual 76.5
        # _groq_entry(GROUP, "moonshotai/kimi-k2-instruct", timeout_s=120),                        # DISABLED 2026-04-24 — not in Groq's actual catalog (research agent hallucinated; Groq listing confirmed missing). AAII 49 (K2-0905), 256K ctx
        # _deepseek_entry(GROUP, "deepseek-v4-flash", timeout_s=120),                              # DISABLED 2026-04-24 — "Insufficient Balance" (same DeepSeek account as v4-pro). AAII 47 (Max), 284B MoE, 1M ctx
        _nim_entry(GROUP, "moonshotai/kimi-k2.5", timeout_s=120),                                  # AAII 47 (R) / 37 (NR) — Arena Elo ~1447, 262K ctx, powers Cursor Composer 2
        _gemini_entry(GROUP, "gemini-3-flash-preview", timeout_s=120),                             # AAII 46 (R) / 35 (NR) — LiveCodeBench 90.8%, SWE-bench 78%, 1M ctx
        _nim_entry(GROUP, "qwen/qwen3.5-397b-a17b", timeout_s=120),                                # AAII 45 (R) / 40 (NR) — MMLU-Pro 87.18%, 262K ctx
        _nim_entry(GROUP, "deepseek-ai/deepseek-v3.2", timeout_s=120),                             # AAII 42 (R) / 32 (NR) — SWE-Bench 72-74%, IMO gold, 163K ctx
        # --- 11–17: Strong second tier (AAII 34–42) ---
        # _sambanova_entry(GROUP, "MiniMax-M2.5", timeout_s=120),                                  # DISABLED 2026-04-24 — SambaNova response: "A payment method is required". Re-enable after adding payment method. AAII 42, SWE-Bench 80.2% (highest SWE), 160K ctx
        _nim_entry(GROUP, "minimaxai/minimax-m2.5", timeout_s=120),                                # AAII 42 — DUP of #11 (same model, NIM infra)
        # _cerebras_entry(GROUP, "zai-glm-4.7", timeout_s=120),                                    # DISABLED 2026-04-24 — 404 "you do not have access to it" (model exists in Cerebras catalog but API key lacks access). AAII 42 (R) / 34 (NR), SOTA τ²-Bench, 200K ctx, 355B params
        _nim_entry(GROUP, "nvidia/nemotron-3-super-120b-a12b", timeout_s=120),                     # AAII 36 — 1M ctx, hybrid Mamba, leads size class on AIME-2025 + Terminal-Bench
        # _sambanova_entry(GROUP, "DeepSeek-V3.1", timeout_s=120),                                 # DISABLED 2026-04-24 (Run-8 evidence) — full SambaNova account now paywalled; whole provider returns "A payment method is required" even for previously-free models. AAII 34 (R) when/if re-enabled.
        _nim_entry(GROUP, "deepseek-ai/deepseek-v3.1-terminus", timeout_s=120),                    # AAII 34 (R) / 29 (NR) — V3.1-Terminus, slightly newer than V3.1, 128K ctx
        _gemini_entry(GROUP, "gemini-3.1-flash-lite-preview", timeout_s=90),                       # AAII 34 — GPQA Diamond 86.9%, 381 t/s, 1M ctx
        # --- 18–21: gpt-oss-120b on four providers (AAII 33 each) ---
        # _cerebras_entry(GROUP, "gpt-oss-120b", timeout_s=120),                                   # DISABLED 2026-04-24 — 404 "you do not have access to it" (model listed in Cerebras catalog but key unauthorized). AAII 33, MMLU-Pro 90.0%, 3000 tok/s
        # _sambanova_entry(GROUP, "gpt-oss-120b", timeout_s=120),                                  # DISABLED 2026-04-24 — SambaNova response: "A payment method is required". Same model as #18 family
        # _groq_entry(GROUP, "openai/gpt-oss-120b", timeout_s=120),                                # DISABLED 2026-04-24 (OP-3) — 8K TPM ceiling permanently incompatible with 30K-token chapter prompts. Run-8 logged every call returning BadRequest("Request too large: Limit 8000, Requested 34127"). AAII 33 still served via NIM's `openai/gpt-oss-120b` entry.
        _nim_entry(GROUP, "openai/gpt-oss-120b", timeout_s=120),                                   # AAII 33 — DUP family; confirmed working on NIM
        # --- 22–23: AAII 30 ---
        # _zhipu_entry(GROUP, "glm-4.7-flash", timeout_s=120),                                     # DISABLED 2026-04-25 (OP-PROVIDER-PRUNE) — Run-16 logged 0/14 success (100% fail), every call returned BadRequestError or NotFoundError. Likely auth or model-name mismatch on the Zhipu OpenAI-compat endpoint; burning a cascade slot for guaranteed failure. AAII 30 (R) was 30B-A3B MoE if it ever worked.
        _gemini_entry(GROUP, "gemini-2.5-flash", timeout_s=60),                                    # OP-25 (2026-04-25): timeout 120→60 — Gemini free tier is 20 req/DAY/model; once exhausted, stays exhausted ~24h. LiteLLM's 60s cooldown can't recover; shorter timeout at least makes the cascade walk past it faster instead of burning the outer 1200s budget. AAII ~30 — GPQA 82.8, MMLU-Lite 88.4, AIME 88, 1M ctx
        # --- 24–28: AAII 22–28 (Mistral cluster + glm-4.5-flash) ---
        _mistral_entry(GROUP, "mistral-small-latest", timeout_s=120),                              # AAII 28 — Mistral Small 4 v26.03, HumanEval 92, MMLU 88.5 (surprisingly > Medium 3.1)
        _mistral_entry(GROUP, "magistral-medium-latest", timeout_s=120),                           # AAII 27 — Magistral 1.2, AIME24 91.82%, GPQA-Diamond 76.3% (reasoning specialist)
        _mistral_entry(GROUP, "mistral-large-latest", timeout_s=120),                              # AAII 23 — Mistral Large 3 v25.12, LMArena #2 OSS, MATH-500 93.6, 256K ctx
        _nim_entry(GROUP, "mistralai/mistral-large-3-675b-instruct-2512", timeout_s=120),          # AAII 23 — DUP of #26 (same Large 3 model, NIM infra)
        # _zhipu_entry(GROUP, "glm-4.5-flash", timeout_s=120),                                     # DISABLED 2026-04-25 (OP-PROVIDER-PRUNE) — Run-16 logged 1/4 success (25%); same Zhipu endpoint failure pattern as glm-4.7-flash. AAII ~23.
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
        # Combined model_list — `kd-all` (synthesis/planner/critic), `kd-keylm`
        # (KeyLLM cluster labels), `kd-reduce-label` (Clio REDUCE per-meta
        # labeling + chapter ordering), and `kd-embed` (embeddings) all live
        # in one Router so they share the cooldown circuit-breaker + Redis
        # state. The factory + helper functions select which group via the
        # `model=` arg on ChatLiteLLMRouter / Router.embedding.
        model_list=(
            _all_entries()
            + _keylm_entries()
            + _reduce_label_entries()
            + _embed_entries()
        ),
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


def build_keylm_chain():
    """
    Tiny-LM chain for the classical MAP step's cluster-label generation
    (KeyLLM-style). Routes to KEYLM_GROUP — currently NIM Llama-3.2-1B
    primary, Groq Llama-3.2-1B-preview fallback. T=0.0 for deterministic
    output; max_tokens applied per-call by the caller (typically 16 — a
    2-4 word Title-Case title fits in 8-12 BPE tokens with margin).

    See docs/KD-PLANNER-MAP-OPTIMIZATION.md §5 for the model selection
    rationale. KeyLLM is intentionally NOT routed through the kd-all
    frontier rotator — small task, small model.
    """
    return ChatLiteLLMRouter(router=_get_router(), model=KEYLM_GROUP, temperature=0.0)


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
    kd-all reasoning models that burn the 300s NIM gateway budget on <think>
    blocks for what is structurally a 3K-token classification task.
    """
    return ChatLiteLLMRouter(
        router=_get_router(), model=REDUCE_LABEL_GROUP, temperature=1.0,
    )


