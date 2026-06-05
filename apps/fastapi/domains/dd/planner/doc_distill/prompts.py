"""doc_distill prompt builder — STATIC PREFIX + DYNAMIC SUFFIX shape for
KV cache hits across Groq / Gemini implicit / DeepSeek / NIM providers."""
from __future__ import annotations

from .params import (
    BODY_CHARS_MAX,
    KEY_TERMS_MAX,
    KEY_TERMS_MIN,
    SUMMARY_WORDS_MAX,
    SUMMARY_WORDS_MIN,
)


def build_prompt(framework: str, source_key: str, body: str) -> str:
    """V6 (2026-05-28) — prompt-prefix reordering for KV cache hits across
    Groq + Gemini implicit + DeepSeek + NIM. Static rubric/schema FIRST
    (cacheable prefix shared by all 135+ calls in a corpus), dynamic file
    content LAST. Per-arm KV reuse yields 2-3× TTFT improvement after
    warmup on providers that auto-cache.
    """
    return (
        # ── STATIC PREFIX (KV-cacheable across all calls in this corpus) ──
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
        # ── DYNAMIC SUFFIX (changes per call) ──
        f"FILE: {source_key}\n\n"
        f"--- FILE CONTENT ---\n"
        f"{body[:BODY_CHARS_MAX]}\n"
        f"--- END FILE ---"
    )
