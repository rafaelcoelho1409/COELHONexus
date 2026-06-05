"""doc_distill prompt/cache version — bumped when the prompt or fallback
policy changes so a re-plan re-distills under the new shape.

v2 (2026-05-30) — fallback distillate on LLM-distill failure (Fix #4):
a doc with content but a failed distill is no longer silently dropped
from the book; it gets a deterministic title/identifier-derived
distillate so it flows through chapter_assign + chapter_select.
"""
from __future__ import annotations


PROMPT_VERSION = "v2-fallback-distill-2026-05-30"
