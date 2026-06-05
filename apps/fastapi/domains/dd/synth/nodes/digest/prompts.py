"""digest_construct — LLM prompt builders (per-source digest + repair)."""
from __future__ import annotations


def _format_outline_compact(outline_sections: list[dict]) -> str:
    """Compact outline view for the digest + repair prompts."""
    lines: list[str] = []
    for s in outline_sections:
        sid = s.get("section_id", "?")
        heading = s.get("heading", "?")
        desc = s.get("description", "?")
        lines.append(f"  [{sid}] {heading}\n      {desc}")
    return "\n".join(lines)


def build_digest_prompt(
    *,
    chapter_id: str,
    chapter_title: str,
    framework: str,
    outline_sections: list[dict],
    source_key: str,
    source_md: str,
    source_vault_hashes: list[str],
) -> str:
    """Build the per-source digest prompt. The LLM sees ONE source at a
    time + the FULL outline. It decides which sections this source
    contributes to (multi-label, with a relevance grade per assignment)
    and routes vault sentinels."""
    outline_block = _format_outline_compact(outline_sections)
    hash_block = (
        "\n".join(f"  - {h}" for h in source_vault_hashes)
        if source_vault_hashes
        else "  (none — this source has no fenced code blocks)"
    )
    return (
        f"You are the Digest Constructor — step 4 of the Docs Distiller "
        f"synth pipeline. The chapter outline has already been "
        f"decomposed by outline_sdp (step 3). Your job is to read ONE "
        f"source page and decide which sections it contributes to + "
        f"WHAT specifically.\n\n"

        f"This is per-source LLM-assigned routing (replaces deprecated "
        f"Phase B cosine routing). Borrows the per-source-digest pattern "
        f"from LLMxMapReduce-V3 (arXiv 2510.10890) + the typed paper-card "
        f"schema from IterSurvey (arXiv 2510.21900). Your output drives "
        f"sawc_write's section drafting downstream.\n\n"

        f"FRAMEWORK: {framework}\n"
        f"CHAPTER: {chapter_id} — {chapter_title}\n\n"

        f"== OUTLINE SECTIONS (each is `[id] heading — description`) ==\n"
        f"{outline_block}\n"
        f"== END OUTLINE ==\n\n"

        f"== SOURCE: {source_key} ==\n"
        f"VAULT HASHES PRESENT IN THIS SOURCE "
        f"({len(source_vault_hashes)}):\n"
        f"{hash_block}\n\n"

        f"SOURCE MARKDOWN:\n"
        f"{source_md}\n"
        f"== END SOURCE ==\n\n"

        f"== OUTPUT — strict JSON matching this schema ==\n"
        f"{{\n"
        f'  "source_title": "3-200 chars",\n'
        f'  "overall_summary": "1-paragraph what this source is about (30-800 chars)",\n'
        f'  "contributes_to": [\n'
        f'    {{\n'
        f'      "section_id":  "s1",\n'
        f'      "relevance":   "primary" | "supporting" | "tangential",\n'
        f'      "summary":     "1-3 sentences (20-600 chars)",\n'
        f'      "key_facts":   ["fact 1", ...],\n'
        f'      "code_refs":   ["16-hex", ...]\n'
        f'    }},\n'
        f'    ... 0-20 entries ...\n'
        f'  ],\n'
        f'  "unassigned_code_refs": ["16-hex", ...]\n'
        f"}}\n\n"

        f"== HARD RULES ==\n"
        f"1. section_id MUST be one of the outline ids above.\n"
        f"2. OMIT sections this source doesn't actually contribute to.\n"
        f"3. relevance must be HONEST: 'primary' = main authority; "
        f"   'supporting' = useful detail; 'tangential' = mentions in "
        f"   passing. Route each code example to the ONE section it most "
        f"   belongs in (DD-SYNTH-SECTION-RECYCLING-2026-05-29 #5). A "
        f"   source claiming 'primary' in >2 sections is flagged as "
        f"   over-spread.\n"
        f"4. code_refs MUST be 16-hex strings from `vault_hashes_in_source`.\n"
        f"5. A single vault hash can appear in AT MOST ONE contribution.\n"
        f"6. A vault hash cannot appear in BOTH contributes_to AND "
        f"   unassigned_code_refs.\n"
        f"7. summary should be CONCRETE — name actual APIs/types/methods.\n"
        f"8. key_facts are STANDALONE claims — no 'see above' references.\n\n"

        f"== DECOMPOSITION GUIDANCE ==\n"
        f"- Most sources contribute to 1-2 sections, not all of them.\n"
        f"- If the source is broadly about ONE section's topic, that "
        f"section gets 'primary' and others may not appear at all.\n"
        f"- If the source is a reference / catalog covering multiple "
        f"sections, expect 3-5 'supporting' contributions.\n"
        f"- 'tangential' should be rare.\n\n"

        f"Respond ONLY with valid JSON matching the schema above. NO "
        f"prose commentary, NO markdown wrapping, NO explanation."
    )


def build_repair_prompt(
    *,
    chapter_id: str,
    chapter_title: str,
    framework: str,
    outline_sections: list[dict],
    source_key: str,
    source_md: str,
    source_vault_hashes: list[str],
    current_json: str,
    issues: list[str],
) -> str:
    """Repair prompt — given an LLM digest that failed validation, ask
    for a fixed version with the SAME schema."""
    outline_block = _format_outline_compact(outline_sections)
    hash_block = (
        "\n".join(f"  - {h}" for h in source_vault_hashes)
        if source_vault_hashes
        else "  (none)"
    )
    issues_block = "\n".join(f"- {x}" for x in issues)
    return (
        f"Fix structural issues in this source digest. Keep the same "
        f"JSON schema. Preserve good fields; only change what's needed.\n\n"

        f"CHAPTER: {chapter_id} — {chapter_title}\n"
        f"FRAMEWORK: {framework}\n"
        f"SOURCE: {source_key}\n\n"

        f"OUTLINE SECTIONS (use ONLY these section_ids):\n"
        f"{outline_block}\n\n"

        f"VAULT HASHES PRESENT IN THIS SOURCE (use ONLY these for code_refs):\n"
        f"{hash_block}\n\n"

        f"SOURCE MARKDOWN (for context):\n"
        f"{source_md}\n\n"

        f"CURRENT DIGEST:\n{current_json}\n\n"

        f"ISSUES TO FIX:\n{issues_block}\n\n"

        f"Respond ONLY with valid JSON matching the original schema. "
        f"NO commentary, NO markdown wrapping."
    )
