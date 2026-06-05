from __future__ import annotations

import re


# `(N)b` form (case-insensitive). Used by passes_capability_floor to read
# parameter size from a model id like `llama-3.3-70b` or `nemotron-3-super-120b`.
PARAM_SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*b\b", re.IGNORECASE)

# MoE markers — `8x7b`, `8x22`, `128e`, `mixtral`, `moe`, etc. Provider-agnostic
# (validated across NIM/Groq/Gemini/Cerebras/Mistral naming). MoE → always pass
# the capability floor (capable despite low active params).
MOE_RE = re.compile(
    r"\d+\s*x\s*\d+|\b\d+\s*x\b|\d+e\b|\bmoe\b|mixtral|mixture",
    re.IGNORECASE,
)
