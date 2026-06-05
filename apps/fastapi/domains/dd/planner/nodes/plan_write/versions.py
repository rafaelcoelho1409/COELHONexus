"""plan_write version markers — schema version of the plan blob + the
prompt version (no LLM today; bump invalidates cache cleanly)."""
from __future__ import annotations


SCHEMA_VERSION = "1.0"
PROMPT_VERSION = "v1-2026-05-18"
