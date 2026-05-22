from __future__ import annotations

CACHE_PREFIX_SCORES      = "dd:rotator:bench:scores:"
CACHE_PREFIX_LEADERBOARD = "dd:rotator:bench:lb:"
CACHE_PREFIX_CANONICAL   = "dd:rotator:bench:canonical:"
SCORES_TTL_S      = 90 * 24 * 3600
LEADERBOARD_TTL_S =  7 * 24 * 3600
CANONICAL_TTL_S   = 365 * 24 * 3600
HTTP_TIMEOUT_S = 30


# =============================================================================
# Per-step composite-score weights — uses metrics actually retrievable
# =============================================================================
STEP_WEIGHTS: dict[str, dict[str, float]] = {
    # code-heavy prose synthesis
    "dd-synth": {
        "lmarena_coding": 0.30,
        "lmarena":        0.25,
        "aaii":           0.20,
        "gpqa":           0.15,
        "mmlu_pro":       0.10,
    },
    # short structured-output classification
    "dd-reduce-label": {
        "lmarena":  0.35,
        "aaii":     0.30,
        "mmlu_pro": 0.20,
        "gpqa":     0.15,
    },
    # tiny instruct LMs — format adherence + small-model knowledge proxy
    "dd-keylm": {
        "mmlu_pro": 0.45,
        "aaii":     0.35,
        "gsm8k":    0.20,
    },
    # embeddings — no public MTEB-equivalent free source, fall back to general
    # quality. PILOT will diverge embedding ranking from chat ranking quickly.
    "dd-embed": {"lmarena": 1.0},
    # general fallback pool
    "dd-all": {
        "aaii":           0.30,
        "lmarena":        0.25,
        "lmarena_coding": 0.20,
        "mmlu_pro":       0.15,
        "gpqa":           0.10,
    },
    # planning — emphasize reasoning + instruction
    "dd-plan": {
        "lmarena":  0.30,
        "aaii":     0.30,
        "mmlu_pro": 0.20,
        "arc_agi":  0.20,
    },
    # curator — like synth, slightly lighter on code
    "dd-curator": {
        "lmarena":        0.35,
        "lmarena_coding": 0.25,
        "aaii":           0.20,
        "mmlu_pro":       0.20,
    },
    # grader / critic — knowledge + reasoning
    "dd-grader": {
        "aaii":     0.30,
        "lmarena":  0.25,
        "mmlu_pro": 0.20,
        "gpqa":     0.15,
        "hle":      0.10,
    },
    "dd-critic": {
        "aaii":     0.30,
        "lmarena":  0.25,
        "mmlu_pro": 0.20,
        "gpqa":     0.15,
        "hle":      0.10,
    },
}


# Provider-tier ordering — secondary sort key for tied (or unscored) models.
# When composite_score is identical (typically score==0 for models that no
# benchmark source covered), ties break by this order. Reflects empirical
# speed + reliability observations from the v1 catalog and 2026-04 production
# runs. PILOT will eventually override this with learned per-deployment data.
PROVIDER_TIER: dict[str, int] = {
    "groq":      1,    # LPU, sub-100ms TTFT, narrow but fast pool
    "cerebras":  2,    # WSE, fast, narrow pool
    "nim":       3,    # NVIDIA DGX Cloud — reliable, broadest catalog
    "mistral":   4,    # direct API, mid latency
    "gemini":    5,    # Google free tier — strict quotas
    "sambanova": 6,
    "deepseek":  7,
}


# Normalization ranges — raw → [0, 1] (clipped)
SCORE_NORMS: dict[str, tuple[float, float]] = {
    "lmarena":        (700.0, 1500.0),
    "lmarena_coding": (700.0, 1600.0),
    "aaii":           (0.0, 100.0),
    "mmlu_pro":       (0.0, 100.0),
    "gpqa":           (0.0, 100.0),
    "arc_agi":        (0.0, 100.0),
    "gsm8k":          (0.0, 100.0),
    "hle":            (0.0, 100.0),
    "ifeval":         (0.0, 100.0),
    "math":           (0.0, 100.0),
    "bbh":            (0.0, 100.0),
    "humaneval":      (0.0, 100.0),
    "mteb":           (0.0, 100.0),
}


# =============================================================================
# Name normalization — heuristic layer 1
# =============================================================================
# Suffixes stripped to canonicalize variant names.
#
# RULE OF THUMB: strip TUNING/FORMAT/TIMESTAMP suffixes (these are different
# packagings of the same model), but PRESERVE SIZE/CAPABILITY suffixes (these
# are genuinely different models with different benchmark scores).
#
# DO STRIP — tuning, format, deployment-stage, version-stamp:
#   -instruct, -chat, -chat-it, -it       (instruction-tuned variants)
#   -versatile, -latest                   (Groq/Mistral marketing tags)
#   -preview, -preview-thinking           (release-stage flags)
#   -experimental, -instant               (release-stage flags)
#   -thinking, -reasoning                 (mode-switch flags; same weights underneath)
#   -2511, -2512, -2410, ...              (Mistral date stamps)
#
# DO NOT STRIP — size/capability identifiers (kept here for the negative-test
# documentation; removed from the active list):
#   -flash, -flash-lite                   (Gemini SIZE — flash ≠ pro)
#   -lite, -turbo                         (size/speed variants)
#   -nano, -mini, -small, -medium, -large (size identifiers; benchmark scores differ)
_PROVIDER_SUFFIXES = (
    "-2511", "-2512", "-2510", "-2509", "-2507", "-2410", "-2409", "-2408",
    "-versatile",
    "-latest",
    "-experimental",
    "-preview-thinking",
    "-preview",
    "-thinking",
    "-reasoning",
    "-instant",
    "-instruct",
    "-chat-it",
    "-chat",
    "-it",
)


# Layer 3 (HF API search) ONLY fires when the provider_id begins with a
# recognizable HuggingFace organization prefix. This prevents proprietary
# closed-source models (Gemini, GLM, Kimi, MiniMax, DeepSeek-Pro) from being
# resolved to random HF community fine-tunes that happen to share a name
# token — the poisoning failure mode observed 2026-05-14 where Gemini got
# 0/12 coverage because HF search returned `google/gemma-2-9b-it` etc.
# Open-weights models hosted on HF DO have these prefixes in provider_id
# (e.g. `meta/llama-3.3-70b-instruct` on NIM), so they still benefit from L3.
_HF_FRIENDLY_PREFIXES = (
    "meta/", "meta-llama/",
    "mistralai/", "mistral/",
    "microsoft/",
    "google/",                      # gemma open weights, NOT gemini proprietary
    "openai/",                      # gpt-oss family on HF
    "deepseek-ai/",
    "qwen/", "alibaba/",
    "ibm-granite/", "ibm/",
    "snowflake/",
    "stabilityai/",
    "huggingfaceh4/", "huggingface/",
    "togethercomputer/",
    "writer/",
    "01-ai/",                       # yi family
    "bigcode/",
    "tiiuae/",
    "baai/",
)


_OPENLM_COLUMN_MAP: dict[str, str] = {
    "arena elo":     "lmarena",
    "arena score":   "lmarena",
    "coding":        "lmarena_coding",
    "vision":        "lmarena_vision",
    "aaii":          "aaii",
    "intelligence":  "aaii",
    "mmlu-pro":      "mmlu_pro",
    "mmlu pro":      "mmlu_pro",
    "arc-agi":       "arc_agi",
    "arc agi":       "arc_agi",
    "gpqa":          "gpqa",
}


_OPENEVALS_BENCHMARK_MAP: dict[str, str] = {
    "mmlu_pro":  "mmlu_pro",
    "mmlu-pro":  "mmlu_pro",
    "gpqa":      "gpqa",
    "gpqa_diamond": "gpqa",
    "gsm8k":     "gsm8k",
    "hle":       "hle",
    "humanity_last_exam": "hle",
    "humanity's_last_exam": "hle",
    "ifeval":    "ifeval",
    "math":      "math",
    "bbh":       "bbh",
    "humaneval": "humaneval",
}


# NOTE: the `_SOURCES` dispatch table (name -> fetcher) and the mutable runtime
# state (`_known_canonicals`, `_inmem_leaderboards`, `_metric_instruments`) live
# in service.py — they reference functions / hold mutable state, so they are not
# constants.


