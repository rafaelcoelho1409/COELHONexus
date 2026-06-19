from __future__ import annotations


HTTP_TIMEOUT_S = 30

# Lower thresholds collapse same-family variants (e.g. flash vs flash-lite).
FUZZY_THRESHOLD = 95


STEP_WEIGHTS: dict[str, dict[str, float]] = {
    "dd-synth": {
        "lmarena_coding": 0.30,
        "lmarena":        0.25,
        "aaii":           0.20,
        "gpqa":           0.15,
        "mmlu_pro":       0.10,
    },
    "dd-reduce-label": {
        "lmarena":  0.35,
        "aaii":     0.30,
        "mmlu_pro": 0.20,
        "gpqa":     0.15,
    },
    "dd-keylm": {
        "mmlu_pro": 0.45,
        "aaii":     0.35,
        "gsm8k":    0.20,
    },
    # No free MTEB-equivalent source — fall back to general quality.
    "dd-embed": {"lmarena": 1.0},
    "dd-all": {
        "aaii":           0.30,
        "lmarena":        0.25,
        "lmarena_coding": 0.20,
        "mmlu_pro":       0.15,
        "gpqa":           0.10,
    },
    "dd-plan": {
        "lmarena":  0.30,
        "aaii":     0.30,
        "mmlu_pro": 0.20,
        "arc_agi":  0.20,
    },
    "dd-curator": {
        "lmarena":        0.35,
        "lmarena_coding": 0.25,
        "aaii":           0.20,
        "mmlu_pro":       0.20,
    },
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


# Tie-break ordering when composite_score ties.
PROVIDER_TIER: dict[str, int] = {
    "groq":      1,
    "cerebras":  2,
    "nim":       3,
    "mistral":   4,
    "gemini":    5,
    "sambanova": 6,
    "deepseek":  7,
}


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


# Strip tuning/format/timestamp suffixes; preserve size suffixes (-flash, -lite, -nano, -mini).
_PROVIDER_SUFFIXES: tuple[str, ...] = (
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


# Vestigial: L3 HF API canonicalization disabled (HF `downloads` ranking surfaced quantized variants over base models).
_HF_FRIENDLY_PREFIXES: tuple[str, ...] = (
    "meta/", "meta-llama/",
    "mistralai/", "mistral/",
    "microsoft/",
    "google/",
    "openai/",
    "deepseek-ai/",
    "qwen/", "alibaba/",
    "ibm-granite/", "ibm/",
    "snowflake/",
    "stabilityai/",
    "huggingfaceh4/", "huggingface/",
    "togethercomputer/",
    "writer/",
    "01-ai/",
    "bigcode/",
    "tiiuae/",
    "baai/",
)


# OpenLM column → our score field.
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


# OpenEvals column → our score field.
_OPENEVALS_BENCHMARK_MAP: dict[str, str] = {
    "mmlu_pro":             "mmlu_pro",
    "mmlu-pro":             "mmlu_pro",
    "gpqa":                 "gpqa",
    "gpqa_diamond":         "gpqa",
    "gsm8k":                "gsm8k",
    "hle":                  "hle",
    "humanity_last_exam":   "hle",
    "humanity's_last_exam": "hle",
    "ifeval":               "ifeval",
    "math":                 "math",
    "bbh":                  "bbh",
    "humaneval":            "humaneval",
}
