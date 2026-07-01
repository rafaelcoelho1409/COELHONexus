from __future__ import annotations


GROUP              = "dd-all"
KEYLM_GROUP        = "dd-keylm"
REDUCE_LABEL_GROUP = "dd-reduce-label"
SYNTH_GROUP        = "dd-synth"
DD_EMBED_GROUP     = "dd-embed"
# dd-all includes thinking/small models that struggle with the 6-phase orchestration prompt; rr-strong excludes them.
RR_STRONG_GROUP    = "rr-strong"


# NIM doesn't expose llama-embed-nemotron-8b at integrate.api.nvidia.com/v1/embeddings.
DD_EMBED_MODEL_NAME  = "nvidia/llama-nemotron-embed-1b-v2"
DD_RERANK_MODEL_NAME = "nvidia/llama-nemotron-rerank-1b-v2"

_NIM_RERANK_BASE = "https://ai.api.nvidia.com/v1/retrieval"


_SETTINGS_GEN_REDIS_KEY = "dd:rotator:settings_gen"


# Separate cell from "dd-all" so binary classification doesn't average reward
# shape with synthesizer cells.
_JUDGE_KD_PROCESS = "dd-grader"


_LITELLM_PREFIX_TO_PROVIDER: dict[str, str] = {
    "groq":       "groq",
    "nvidia_nim": "nim",
    "cerebras":   "cerebras",
    "mistral":    "mistral",
    "gemini":     "gemini",
    "deepseek":   "deepseek",
    "sambanova":  "sambanova",
}

_PROVIDER_KEY_ENV: dict[str, str] = {
    "nvidia_nim": "NVIDIA_API_KEY",
    "groq":       "GROQ_API_KEY",
    "cerebras":   "CEREBRAS_API_KEY",
    "mistral":    "MISTRAL_API_KEY",
    "gemini":     "GOOGLE_API_KEY",
    "deepseek":   "DEEPSEEK_API_KEY",
    "sambanova":  "SAMBANOVA_API_KEY",
}


# Gemini excluded — LiteLLM translation of response_format breaks on nested Pydantic schemas (native API uses response_mime_type).
_RESPONSE_FORMAT_SAFE_PROVIDERS: tuple[str, ...] = (
    "nvidia_nim/",
    "mistral/",
    "openai/",
    "groq/",
)


# "embed" filter never affects the rotator's own embedder (lives in dd-embed, a separate pool).
_NON_CHAT_MARKERS: tuple[str, ...] = (
    "embed", "bge", "e5-", "-e5", "gte-", "rerank", "deplot", "ocr",
    "whisper", "clip", "siglip", "-vit", "vit-", "guard", "reward",
)

# Separate σ²_ewma evolution from workhorses; bandit picks best heavyweight by writer-specific reward.
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
