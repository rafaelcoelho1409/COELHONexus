"""sawc — LLM prompt builders (writer repair + pairwise critic picker)."""
from infra.langfuse.prompts import with_langfuse_override

from . import domain

PAIRWISE_PICKER_PROMPT = """You are picking the BETTER of two technical-documentation
drafts for the same section. The section is part of a larger distilled book.

Choose by these criteria in order:
1. Checklist coverage (does the draft address every outline point named?)
2. Citation density (does it cite/reference the source documentation it claims?)
3. Structural completeness (no truncations, no orphan code-refs, no placeholder text)
4. Clarity and concision (well-organized, no rambling)

You MUST choose A or B. Ties are NOT allowed.

=== SECTION ===
heading: {section_heading}
expected primary source contributions: {n_primary_contribs}

=== DRAFT A — structural summary ===
{summary_a}

=== DRAFT B — structural summary ===
{summary_b}

Answer in JSON: {{"winner": "A" | "B", "reason": "one short sentence"}}"""


@with_langfuse_override("dd.synth.sawc.repair")
def build_repair_prompt(
    *,
    framework: str,
    chapter_id: str,
    chapter_title: str,
    section_id: str,
    section_heading: str,
    section_description: str,
    section_prerequisites: list[str],
    contributions: list[dict],
    allowed_hashes: list[str],
    valid_source_keys: list[str],
    memory: list[dict],
    current_json: str,
    issues: list[str],
    prose_mode: bool = False,
    mixed_mode: bool = False,
) -> str:
    """Repair prompt: same context as writer + issue list, requesting a corrected output. Wrapped in a LangFuse-managed-override layer (falls back to this local body when no template is published)."""
    mixed = bool(mixed_mode) and bool(allowed_hashes) and not prose_mode
    prose = (prose_mode or not allowed_hashes) and not mixed
    prereqs_str = (
        ", ".join(section_prerequisites)
        if section_prerequisites else "(none)"
    )
    hash_list = (
        "PROSE MODE — this section has NO code. Set EVERY subtopic's "
        "code_ref_hash to \"\" (empty) and write substantial source-grounded "
        "conceptual prose (3-6 distinct subtopics, 40-80 word explanations)."
        if prose
        else (
            "\n".join(f"  - {h}" for h in allowed_hashes)
            if allowed_hashes else "  (none)"
        ) + (
            "\n  MIXED MODE: use each hash above in exactly ONE subtopic; every "
            "other subtopic is prose — code_ref_hash \"\" (empty). Keep ≥3 "
            "subtopics in total."
            if mixed else ""
        )
    )
    source_list = (
        "\n".join(f"  - {k}" for k in valid_source_keys)
        if valid_source_keys else "  (none)"
    )
    issues_block = "\n".join(f"- {x}" for x in issues)
    if prose:
        code_hash_hint = ""
    elif mixed:
        code_hash_hint = '16-hex, or "" for a prose subtopic'
    else:
        code_hash_hint = "16-hex"
    return (
        f"Fix structural issues in this cookbook-schema section draft. "
        f"Keep the same v2 schema (heading + intro + subtopics + citations). "
        f"Preserve good subtopics and citations; ONLY change what's "
        f"needed to clear the issues below.\n\n"

        f"FRAMEWORK: {framework}\n"
        f"CHAPTER: {chapter_id} — {chapter_title}\n"
        f"SECTION: {section_id} — {section_heading}\n"
        f"GOAL: {section_description}\n"
        f"PREREQUISITES: {prereqs_str}\n\n"

        f"ALLOWED VAULT HASHES (use ONLY these for subtopics[*].code_ref_hash):\n"
        f"{hash_list}\n\n"

        f"VALID CITATION SOURCE_KEYS (use ONLY these for citations):\n"
        f"{source_list}\n\n"

        f"CONTRIBUTIONS (for grounding):\n"
        f"{domain.format_contributions_block(contributions)}\n\n"

        f"MEMORY:\n{domain.format_memory_block(memory)}\n\n"

        f"CURRENT DRAFT:\n{current_json}\n\n"

        f"ISSUES TO FIX:\n{issues_block}\n\n"

        f"Schema reminder: {{\n"
        f'  "heading": "...",\n'
        f'  "intro": "1-2 sentence section framing",\n'
        f'  "subtopics": [\n'
        f'    {{"subheading": "2-10 words", "explanation": "8-80 words", '
        f'"code_ref_hash": "{code_hash_hint}"}},\n'
        f'    ... 3-12 entries ...\n'
        f'  ],\n'
        f'  "citations": [{{"source_key": "...", "claim": "..."}}, ...]\n'
        f"}}\n\n"

        f"Respond ONLY with valid JSON matching the v2 schema. "
        f"NO commentary, NO markdown wrapping."
    )
