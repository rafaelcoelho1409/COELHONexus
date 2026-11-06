"""off_topic — bump when the judge's rubric or request changes, so per-page verdicts of the old rubric are not reused."""
from __future__ import annotations


# v1 (2026-09 .. 2026-10): one free-text KEEP/DROP prompt per page, parsed with a regex.
# v2 (2026-11-05): the judge is a deep agent with a structured verdict (`PageRelevance`); verdicts are stored per distinct page text.
PROMPT_VERSION = "v2-deep-agent-2026-11-05"
