"""plan_write version markers — schema version of the plan blob + the
prompt version (no LLM today; bump invalidates cache cleanly)."""
from __future__ import annotations


SCHEMA_VERSION = "1.1-nested-outline-fix"
PROMPT_VERSION = "v2-nested-outline-fix-2026-06-06"
