from __future__ import annotations


# NIM doesn't publish a strict limit; 64 is empirically safe. Helpers auto-batch.
DD_EMBED_BATCH_SIZE = 64

# Override via env KD_DYNAMIC_MIN_PARAM_B (0 = include all). MoE bypasses.
_DYNAMIC_MIN_PARAM_B: float = 20.0
_DYNAMIC_QUALITY_FLOOR_STEPS: tuple[str, ...] = ("dd-all", "dd-synth")

# In-process per-arm cooldown after a 429. Lazy-prune on each call.
_ARM_COOLDOWN_S = 60.0

# Throttle window for the Redis settings-gen GET (cross-worker BYOK propagation).
_GEN_THROTTLE_S = 10.0


# Per-chapter pinning caps per provider — distributes pins across providers.
_PROVIDER_CHAPTER_CAPS: dict[str, int] = {
    "nvidia_nim": 2,
    "groq":       2,
    "cerebras":   2,
    "mistral":    3,
    "gemini":     1,
}


# Surfaced verbatim by every embed/rerank entry point — actionable BYOK message.
_NIM_REQUIRED_MSG = (
    "NVIDIA_API_KEY is not set (Settings store or env). NVIDIA NIM powers the "
    "mandatory embedding + reranking models — add your NVIDIA NIM key in "
    "Settings before running Docs Distiller."
)
