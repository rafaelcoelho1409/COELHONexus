"""Prompt builder: static rubric prefix before dynamic file content for KV-cache reuse across 135+ corpus calls (Groq, Gemini, DeepSeek, NIM)."""
from __future__ import annotations

from .params import (
    BODY_CHARS_MAX,
    KEY_TERMS_MAX,
    KEY_TERMS_MIN,
    SUMMARY_WORDS_MAX,
    SUMMARY_WORDS_MIN,
)


def build_prompt(framework: str, source_key: str, body: str) -> str:
    """Static rubric prefix FIRST, dynamic file content LAST: yields 2-3× TTFT after warmup on auto-cache providers (Groq, Gemini, DeepSeek, NIM)."""
    return (
        # Static prefix — KV-cacheable across all corpus calls.
        f"You are summarizing ONE documentation file from the "
        f"{framework} corpus for use in chapter planning.\n\n"
        f"OUTPUT FORMAT — STRICT JSON:\n"
        f"{{\n"
        f'  "summary":   "ONE sentence ({SUMMARY_WORDS_MIN}-'
        f'{SUMMARY_WORDS_MAX} words) — what does THIS file teach? '
        f'Name the specific feature/command/concept. Avoid generic framing.",\n'
        f'  "key_terms": ["term1", ..., "termN"]  /* '
        f'{KEY_TERMS_MIN}-{KEY_TERMS_MAX} technical identifiers visible in '
        f'the file: function names, class names, CLI subcommands, config '
        f'keys, type names. NOT generic words like "function" or '
        f'"configuration". */\n'
        f"}}\n\n"
        f"Respond ONLY with valid JSON. No prose, no markdown wrap.\n\n"
        # Dynamic suffix — changes per call, so placed last.
        f"FILE: {source_key}\n\n"
        f"--- FILE CONTENT ---\n"
        f"{body[:BODY_CHARS_MAX]}\n"
        f"--- END FILE ---"
    )
