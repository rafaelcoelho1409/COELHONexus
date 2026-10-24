"""langfuse params — tunables (no behavior)."""
from __future__ import annotations


# Prompt-template cache TTL (prompt label deployments change rarely;
# 60s bounds staleness without re-fetching per call).
PROMPT_CACHE_TTL_S = 60

# Span-attribute size caps — keep trace payloads bounded.
JSON_ATTR_CAP = 12_000
METADATA_VALUE_CAP = 512
