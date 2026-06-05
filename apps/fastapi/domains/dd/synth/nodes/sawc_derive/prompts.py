"""sawc_derive — LLM prompt builders (analogical expansion + re-explain)."""
from __future__ import annotations


def build_reexplain_prompt(
    *,
    framework: str,
    section_heading: str,
    subheading: str,
    old_explanation: str,
    derived_code: str,
    lang: str = "python",
) -> str:
    """Ship D (2026-05-25): after MPSC promotes a derived code block,
    the original explanation (written for the thin signature) is stale —
    it describes APIs/params from the signature, not the expanded
    example. This prompt regenerates the explanation conditioned on the
    new code body.

    Per the deep research (Citation-Grounded Code Comprehension arXiv
    2512.12117): prose grounded to the resolved code beats prose
    grounded to an imagined topic.
    """
    return (
        f"You are regenerating ONE documentation explanation in a "
        f"{framework} learning resource. The code block below has been "
        f"newly AI-generated to expand a thin signature; the old "
        f"explanation no longer describes it. Write a fresh explanation "
        f"that grounds to THIS specific code.\n\n"
        f"SECTION: {section_heading}\n"
        f"SUBTOPIC: {subheading}\n\n"
        f"OLD EXPLANATION (stale — describes a different example):\n"
        f"{old_explanation.strip()}\n\n"
        f"NEW CODE BLOCK:\n"
        f"```{lang}\n{derived_code.strip()}\n```\n\n"
        f"== TASK ==\n"
        f"Write a NEW explanation (8-80 words, 1-3 sentences) that:\n"
        f"  1. Describes WHAT this specific code block demonstrates.\n"
        f"  2. References at least ONE identifier visible in the code "
        f"(function name, decorator, type, parameter, or imported "
        f"symbol).\n"
        f"  3. Reads as prose that goes IMMEDIATELY BEFORE the code in a "
        f"cookbook chapter.\n"
        f"  4. NO code fences, NO inline `code-ref` tags, NO meta-framing "
        f"('In this example...'). Just the explanation.\n\n"
        f"OUTPUT: strict JSON, exactly: "
        f'{{"explanation": "your rewritten 8-80 word explanation here"}}\n'
        f"NO prose commentary outside JSON."
    )


def build_analogical_prompt(
    *,
    framework: str,
    chapter_title: str,
    section_heading: str,
    subheading: str,
    explanation: str,
    original_body: str,
    original_lang: str = "python",
) -> str:
    """Analogical Prompting prompt — ask the LLM to first reason about a
    relevant, expanded example by analogy, then emit it as a fenced code
    block.

    Per Yasunaga et al. 2023 ("Large Language Models as Analogical
    Reasoners"), letting the model first describe a closely-related
    canonical example improves derived-code quality vs. one-shot
    generation. We don't need the reasoning text in the output — we
    strip everything outside the final fenced block server-side.

    Output contract: exactly one fenced code block in the response.
    Anything outside the fence is discarded.
    """
    return (
        f"You are expanding a thin documentation reference into a "
        f"COMPLETE RUNNABLE EXAMPLE for a {framework} learning resource.\n\n"
        f"CHAPTER: {chapter_title}\n"
        f"SECTION: {section_heading}\n"
        f"SUBTOPIC: {subheading}\n"
        f"PROSE LEAD-IN (already written, do NOT repeat): "
        f"{explanation}\n\n"
        f"== ORIGINAL DOC REFERENCE (too thin to teach) ==\n"
        f"```{original_lang}\n"
        f"{original_body.strip()}\n"
        f"```\n\n"
        f"== TASK ==\n"
        f"Think about ONE common production use-case that exercises this "
        f"API. By analogy to that use-case, write a self-contained, "
        f"runnable {original_lang} example demonstrating realistic usage. "
        f"Show real imports, real arguments, real return-value handling.\n\n"
        f"== HARD RULES ==\n"
        f"1. Output EXACTLY ONE fenced ```{original_lang} ... ``` block. "
        f"NO prose before, after, or between fences.\n"
        f"2. The code MUST parse as valid {original_lang} (AST validates "
        f"it server-side; ungated samples are discarded).\n"
        f"3. Length: 4-50 non-blank lines. Tight, focused, teachable.\n"
        f"4. INCLUDE imports for any types/decorators used.\n"
        f"5. Use REAL function/method names from {framework} — do NOT "
        f"invent APIs. If unsure, mirror the surface from the original "
        f"reference above; expand parameter names + types realistically.\n"
        f"6. NO placeholders like '...', 'YOUR_KEY_HERE', '# TODO'. "
        f"Concrete, usable values everywhere.\n"
        f"7. NO test scaffolding (no `assert`, no `unittest`, no "
        f"`pytest.mark`). Production-style code only.\n"
        f"8. NO inline comments explaining what the code does line-by-"
        f"line — the prose lead-in already framed it.\n\n"
        f"Respond with the fenced code block ONLY."
    )
