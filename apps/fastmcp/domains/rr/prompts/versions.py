"""Prompt/schema version markers — per docs/CODE-CONVENTIONS.md §2.

Cache-invalidation knobs: bump a marker whenever the artifact it versions
changes shape. Kept apart from `prompts.py` (template text) and `keys.py`
(routing names) so a version bump touches neither template nor routing code.
"""
from __future__ import annotations


DIGEST_TODAY_PROMPT_VERSION: str = "2026-06-12"
