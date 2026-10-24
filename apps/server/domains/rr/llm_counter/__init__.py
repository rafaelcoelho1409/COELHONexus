"""rr/llm_counter — per-scan LLM call/token counters. Mirrors
`domains.ycs.llm_counter`'s keys.py / params.py / service.py (I/O) split;
response parsing is shared (`domains.settings.llm_usage`)."""
from __future__ import annotations
from . import keys, params, service
