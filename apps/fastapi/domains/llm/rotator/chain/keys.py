from __future__ import annotations


# --------------------------------------------------------------------------- #
# Router group names (public — used as model= arg on the Router/chains)
# --------------------------------------------------------------------------- #
GROUP              = "dd-all"
KEYLM_GROUP        = "dd-keylm"
REDUCE_LABEL_GROUP = "dd-reduce-label"
SYNTH_GROUP        = "dd-synth"
DD_EMBED_GROUP     = "dd-embed"
# Research Radar (step 5b — 2026-06-12): strong-tier curated pool for the
# RR orchestrator. The dd-all pool contains thinking/reasoning models +
# small models that struggle with the 6-phase orchestration prompt. This
# pool is hand-curated to models proven to handle tool_calls reliably
# (Cerebras Llama 70B + NIM 70B+ that returned 200 OK on RR smoke runs).
RR_STRONG_GROUP    = "rr-strong"


# --------------------------------------------------------------------------- #
# Model identifiers (public)
# --------------------------------------------------------------------------- #
# 2026-05-23: REVERTED from llama-embed-nemotron-8b — NIM doesn't expose that
# model at integrate.api.nvidia.com/v1/embeddings (404). Sticking with the
# proven baseline. nv-embed-v1 + nv-embedqa-mistral-7b-v2 are alternates if
# this ever needs to move (probe /v1/models first).
DD_EMBED_MODEL_NAME  = "nvidia/llama-nemotron-embed-1b-v2"
DD_RERANK_MODEL_NAME = "nvidia/llama-nemotron-rerank-1b-v2"

_NIM_RERANK_BASE = "https://ai.api.nvidia.com/v1/retrieval"


# --------------------------------------------------------------------------- #
# Redis keys
# --------------------------------------------------------------------------- #
_SETTINGS_GEN_REDIS_KEY = "dd:rotator:settings_gen"


# --------------------------------------------------------------------------- #
# Bandit dd_process namespace for judge calls
# --------------------------------------------------------------------------- #
# Separate from "dd-all" so binary classification cells don't average reward
# shape with synthesizer cells (different latency expectations + reward shapes).
_JUDGE_KD_PROCESS = "dd-grader"


# --------------------------------------------------------------------------- #
# Provider id ↔ env var name maps
# --------------------------------------------------------------------------- #
# LiteLLM model-prefix → registry provider id (settings.json uses registry ids).
_LITELLM_PREFIX_TO_PROVIDER: dict[str, str] = {
    "groq":       "groq",
    "nvidia_nim": "nim",
    "cerebras":   "cerebras",
    "mistral":    "mistral",
    "gemini":     "gemini",
    "deepseek":   "deepseek",
    "sambanova":  "sambanova",
}

# LiteLLM model-prefix → env-var name. Used by the per-call cascade in
# chat_judge_bandit_async to resolve the right provider key per deployment.
_PROVIDER_KEY_ENV: dict[str, str] = {
    "nvidia_nim": "NVIDIA_API_KEY",
    "groq":       "GROQ_API_KEY",
    "cerebras":   "CEREBRAS_API_KEY",
    "mistral":    "MISTRAL_API_KEY",
    "gemini":     "GOOGLE_API_KEY",
    "deepseek":   "DEEPSEEK_API_KEY",
    "sambanova":  "SAMBANOVA_API_KEY",
}


# --------------------------------------------------------------------------- #
# Provider prefixes — capability sets
# --------------------------------------------------------------------------- #
# Providers known to accept the OpenAI-style response_format={"type":
# "json_schema", ...} via LiteLLM. Gemini intentionally excluded — its native
# API uses response_mime_type+response_schema; LiteLLM's translation has rough
# edges on nested Pydantic schemas.
_RESPONSE_FORMAT_SAFE_PROVIDERS: tuple[str, ...] = (
    "nvidia_nim/",
    "mistral/",
    "openai/",
    "groq/",
)


# --------------------------------------------------------------------------- #
# Model substring filters
# --------------------------------------------------------------------------- #
# Non-chat models that live discovery returns but can't serve chat/structured
# generation (embedders, rerankers, vision encoders, OCR/ASR/TTS, safety
# classifiers, reward models). NOTE: the rotator's own embedder lives in the
# separate dd-embed pool — excluding "embed" here never touches embeddings.
_NON_CHAT_MARKERS: tuple[str, ...] = (
    "embed", "bge", "e5-", "-e5", "gte-", "rerank", "deplot", "ocr",
    "whisper", "clip", "siglip", "-vit", "vit-", "guard", "reward",
)

# SAWC writer pool (dd-synth-write) — heavyweight reasoning models only.
# Per Option B (2026-05-24): writer drafts get separate σ²_ewma evolution +
# workhorses excluded; bandit picks best heavyweight by writer-specific reward.
DD_SYNTH_WRITE_HEAVYWEIGHTS: tuple[str, ...] = (
    "llama-4-maverick",
    "qwen3.5-397b",
    "z-ai/glm-5.1",
    "moonshotai/kimi",
    "nemotron-3-super",
    "minimaxai/minimax",
    "mistral-large",
    "deepseek-v4",
    "gpt-oss-120b",
    "magistral-medium",
    "devstral-medium",
)
