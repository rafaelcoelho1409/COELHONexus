"""rr/llm_counter — per-scan LLM call/token counters (Postgres snapshot). Kept apart from the shared
`domains.settings.llm_counter` until rr is removed; response parsing is shared with it."""
from __future__ import annotations
from . import keys, params, service
