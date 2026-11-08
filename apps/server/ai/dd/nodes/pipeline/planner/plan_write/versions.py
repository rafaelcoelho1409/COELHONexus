"""plan_write version markers — schema version of the plan blob + the
prompt version (no LLM today; bump invalidates cache cleanly)."""
from __future__ import annotations


SCHEMA_VERSION = "1.2-plan-health-source-fingerprints"
PROMPT_VERSION = "v3-plan-health-source-fingerprints-2026-11-08"
