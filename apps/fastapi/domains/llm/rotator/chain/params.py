from __future__ import annotations


# NIM doesn't publish a strict limit; 64 is empirically safe. Helpers auto-batch.
DD_EMBED_BATCH_SIZE = 64

# Quality floor for the "All free" heavy pools — see _DYNAMIC_QUALITY_FLOOR_STEPS.
# Override via env KD_DYNAMIC_MIN_PARAM_B (0 = include all). MoE markers always
# bypass this floor — capable despite low active params.
_DYNAMIC_MIN_PARAM_B: float = 20.0
_DYNAMIC_QUALITY_FLOOR_STEPS: tuple[str, ...] = ("dd-all", "dd-synth")

# In-process per-arm cooldown after a 429. Subsequent cascade picks in the same
# burst-window skip cooling arms. Cooldown self-expires; lazy prune on each call.
_ARM_COOLDOWN_S = 60.0

# Settings-generation throttle. _get_router does at most one Redis GET per
# this window per process to learn whether the user's BYOK selection moved.
_GEN_THROTTLE_S = 10.0


# Per-chapter pinning caps per provider — distributes pins across providers so
# the per-process asyncio semaphore in helpers.py doesn't serialize same-provider
# pins. Matches helpers._PROVIDER_CONCURRENCY (per-process limit); chapter slot
# is conservative — fewer concurrent chapters per provider than concurrent calls.
_PROVIDER_CHAPTER_CAPS: dict[str, int] = {
    "nvidia_nim": 2,
    "groq":       2,
    "cerebras":   2,
    "mistral":    3,
    "gemini":     1,
}


# Surfaced verbatim by every embed/rerank entry point — actionable error so the
# user knows it's a BYOK config issue, not a network blip.
_NIM_REQUIRED_MSG = (
    "NVIDIA_API_KEY is not set (Settings store or env). NVIDIA NIM powers the "
    "mandatory embedding + reranking models — add your NVIDIA NIM key in "
    "Settings before running Docs Distiller."
)
