"""Prompt builder: static rubric prefix before dynamic file content for KV-cache reuse across 135+ corpus calls (Groq, Gemini, DeepSeek, NIM)."""
from __future__ import annotations
from . import params



def build_prompt(
    framework: str, source_key: str, body: str, *,
    max_chars: int = params.BODY_CHARS_MAX,
) -> str:
    """Static rubric prefix FIRST, dynamic file content LAST: yields 2-3× TTFT after warmup on auto-cache providers (Groq, Gemini, DeepSeek, NIM).

    `max_chars` defaults to the full-page cap but is overridable — `service.
    distill_one` passes a smaller value on a transient-failure retry (same
    shrink-on-retry shape as `synth/nodes/digest`)."""
    return (
        # Static prefix — KV-cacheable across all corpus calls.
        f"You are summarizing ONE documentation file from the "
        f"{framework} corpus for use in chapter planning.\n\n"
        f"OUTPUT FORMAT — STRICT JSON:\n"
        f"{{\n"
        f'  "summary":   "ONE sentence ({params.SUMMARY_WORDS_MIN}-'
        f'{params.SUMMARY_WORDS_MAX} words) — what does THIS file teach? '
        f'Name the specific feature/command/concept. Avoid generic framing.",\n'
        f'  "key_terms": ["term1", ..., "termN"]  /* '
        f'{params.KEY_TERMS_MIN}-{params.KEY_TERMS_MAX} technical identifiers visible in '
        f'the file: function names, class names, CLI subcommands, config '
        f'keys, type names. NOT generic words like "function" or '
        f'"configuration". */\n'
        f'  "role":      "overview" | "cross_cutting" | "tutorial" | "how_to" | '
        f'"reference" | "explanation"  /* what KIND of page this is: '
        f'overview = ONLY a page that introduces the {framework} framework AS A WHOLE '
        f'(what it is, why it exists, its architecture, how the docs are organised, '
        f'a top-level landing page). A landing, index or catalog page for ONE '
        f'sub-area — a single provider/integration, a list of loaders, models, '
        f'plugins or API symbols — is NOT an overview: label it reference. '
        f'cross_cutting = platform support, debugging/diagnostics, '
        f'performance, threading or compatibility notes that apply across the whole '
        f'framework instead of one topic; tutorial = guided lesson; how_to = task '
        f'recipe; reference = API/option listing; explanation = concepts and design '
        f'rationale. When unsure, choose reference. */\n'
        f"}}\n\n"
        f"Respond ONLY with valid JSON. No prose, no markdown wrap.\n\n"
        # Dynamic suffix — changes per call, so placed last.
        f"FILE: {source_key}\n\n"
        f"--- FILE CONTENT ---\n"
        f"{body[:max_chars]}\n"
        f"--- END FILE ---"
    )
