"""rr/runtime/llm_counter — per-scan LLM call/token counters. Mirrors
`domains.ycs.runtime.llm_counter`'s domain.py (pure) / keys.py / params.py
/ service.py (I/O) split."""
from __future__ import annotations
from . import domain, keys, params, service
