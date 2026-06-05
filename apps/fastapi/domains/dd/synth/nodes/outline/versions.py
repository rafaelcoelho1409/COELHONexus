"""outline_sdp — schema + prompt cache-invalidation markers.

v4 (2026-05-29 PM, DD-SYNTH-SECTION-COUNT) — section-count overhaul.
Bumped to invalidate the outline cache after reconciling the previous
constants deadlock (Pydantic min vs adaptive cap vs hard-trim floor).
"""
from __future__ import annotations


OUTLINE_SCHEMA_VERSION = "1.0"
OUTLINE_PROMPT_VERSION = "v4-adaptive-sections-2026-05-29"
