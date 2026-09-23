"""outline prompts — LLM prompt builders (moved from `domain.py` so the
override decorator's cached fetch doesn't contaminate the pure core).

Local builders stay the source of truth; LangFuse is the additive layer.
"""
from __future__ import annotations
import infra
from . import params



@infra.langfuse.prompts.with_langfuse_override("dd.synth.outline")
def build_outline_prompt(
    *,
    framework: str,
    chapter_id: str,
    chapter_title: str,
    chapter_description: str,
    n_vault_hashes: int,
    sources_concat_md: str,
    target_sections_hint: int = 8,
) -> str:
    """Build the OUTLINE_SDP prompt. target_sections_hint = adaptive per-chapter cap — over-sectioning forces code recycling stripped into hollow cross-refs."""
    return (
        f"You are the Chapter Outliner — `outline_sdp`, step 3 of the "
        f"Docs Distiller synth pipeline. Per SurveyGen-I PlanEvo "
        f"(arXiv 2508.14317 §3.1), your single job is to PRE-DECOMPOSE "
        f"the chapter into about {target_sections_hint} sections "
        f"(TARGET = {target_sections_hint}, sized to this chapter's source "
        f"pool; hard range 2-40) with EXPLICIT inter-section dependencies. "
        f"Emitting MORE than ~{target_sections_hint} sharply-distinct "
        f"sections for this chapter is almost always wrong — the extra "
        f"sections end up covering the same APIs/config as the others and "
        f"get stripped to hollow cross-references downstream. Fewer, "
        f"well-developed sections beat many overlapping ones. You write NO "
        f"prose bodies and place NO code — that happens downstream in "
        f"`sawc_write` after `digest_construct` routes the source "
        f"material to your sections.\n\n"

        f"FRAMEWORK: {framework}\n"
        f"CHAPTER: {chapter_id} — {chapter_title}\n"
        f"CHAPTER GOAL: {chapter_description}\n"
        f"VAULT SIZE (estimate): {n_vault_hashes} code blocks across "
        f"the source material below.\n\n"

        f"== SOURCE MATERIAL (already normalized; vault sentinels like "
        f"`<code-ref hash = \"...\"/>` may appear — IGNORE them, "
        f"digest_construct handles routing) ==\n"
        f"{sources_concat_md}\n"
        f"== END SOURCE MATERIAL ==\n\n"

        f"OUTPUT — strict JSON matching this schema:\n"
        f"{{\n"
        f'  "sections": [\n'
        f'    {{\n'
        f'      "section_id":    "s1",   /* lowercase s + integer; s1, s2, ... */\n'
        f'      "heading":       "2-8 words, topic-specific, no leading #",\n'
        f'      "description":   "1-line topic spec, 20-400 chars",\n'
        f'      "prerequisites": ["s_id", ...],  /* 0-3 ids of EARLIER sections */\n'
        f'      "needs_code":    true            /* false for design narratives */\n'
        f'    }},\n'
        f'    ... ~{target_sections_hint} entries (hard range 2-40) ...\n'
        f'  ]\n'
        f"}}\n\n"

        f"== HARD RULES ==\n"
        f"1. section_id format: 's' + integer ONLY — 's1', 's2', ..., "
        f"'s40'. Unique within the chapter. Once assigned, an id is "
        f"referenced by downstream nodes — do NOT renumber on subsequent "
        f"rewrites. WRONG (rejected every time): a descriptive slug like "
        f"'config-hierarchy-scopes' or 'file-level-customization' — a "
        f"human-readable name is NOT a section_id, no matter how well it "
        f"names the topic. The topic name belongs in `heading`, not here.\n"
        f"2. heading: 2-8 words, topic-y/code-y, NO leading '#'. WRONG "
        f"(rejected every time): cramming the whole section scope into "
        f"one comma-separated list, e.g. 'Extending Claude Code with "
        f"Skills, Hooks, System Prompts, and Project Context' (11 words, "
        f"reads like 4 topics glued together). RIGHT: pick the ONE most "
        f"central topic and name just that, e.g. 'Skills and Hooks' — "
        f"split the rest into their own section(s) instead of listing "
        f"them all in one heading. BANNED (case-insensitive — these are "
        f"content-types, not topics): {params.BANNED_LIST_HUMAN}.\n"
        f"3. description: 20-400 chars, ONE specific topic. Used by "
        f"`digest_construct` to route source material — vague descriptions "
        f"cause mis-routing. Examples of good: 'how to wire DI overrides "
        f"for tests'; 'the streaming response shape for tool calls'. "
        f"Examples of bad: 'various features'; 'examples and usage'.\n"
        f"4. prerequisites: list 0-3 section_ids the reader must absorb "
        f"BEFORE this one. STRUCTURAL deps only (e.g. 's3 uses the "
        f"runnable lifecycle defined in s1' → s1 ∈ s3.prerequisites), "
        f"not merely thematic. The FIRST logical section (lowest stage) "
        f"MUST have an empty list. Subsequent sections MAY have 0 "
        f"prereqs if they're independent of prior sections.\n"
        f"5. Prerequisites form a DAG: NO cycles, NO self-references, NO "
        f"forward references (only point BACKWARD to EARLIER sections in "
        f"the reader's path). Aim for max-depth 3-4 stages — deeper DAGs "
        f"linearize the chapter (kills parallel writing downstream).\n"
        f"6. needs_code: true if the section will reference code patterns / "
        f"APIs / configs / runnable examples. false for pure design "
        f"narrative, ecosystem context, or conceptual material.\n"
        f"7. **SCOPE ORTHOGONALITY (DD-SYNTH-SECTION-RECYCLING-2026-05-29)**: "
        f"every section must teach a DISTINCT capability. Two sections that "
        f"would draw on the SAME APIs / commands / config / code examples "
        f"are ONE section — even when their headings read differently. "
        f"Before emitting, check EVERY pair of sections: if you cannot name "
        f"a concrete code example that belongs to section A but NOT to "
        f"section B, MERGE them. Anti-example (DO NOT do this): a chapter "
        f"with both 'Session Management' AND 'Remote Control' where both "
        f"cover persist / resume / InMemory store / S3 / continue — those "
        f"are the SAME scope and must be ONE section. Overlapping sections "
        f"force the writer to recycle identical code; the renderer then "
        f"strips the duplicates, leaving hollow 'see other section' "
        f"chapters. Fewer, sharply-distinct sections beat many overlapping "
        f"ones.\n\n"

        f"== DECOMPOSITION GUIDANCE ==\n"
        f"- Each section should cover ~5-15 vault hashes (estimate from "
        f"natural topical clusters in the source: fences under a common "
        f"heading, one API surface, one config concern, one error mode).\n"
        f"- Prefer DEEPER-AND-NARROWER (split a too-broad section into 2 "
        f"with a prereq edge between them) over WIDER-AND-FLATTER (one "
        f"section trying to cover 25 fences).\n"
        f"- Encourage parallelism by keeping prereq chains short — many "
        f"shallow branches > one long sequential spine.\n"
        f"- Order sections by reading flow: stage 0 (no prereqs) → "
        f"stage 1 → ... Each prereq chain represents one teaching arc.\n"
        f"- DO NOT duplicate topics across sections — `digest_construct` "
        f"routes each source artifact to exactly ONE section; overlapping "
        f"headings force an arbitrary pick.\n\n"

        f"Respond ONLY with valid JSON matching the schema above. NO "
        f"prose commentary, NO markdown wrapping, NO explanation — the "
        f"JSON is parsed directly by the next graph node."
    )


@infra.langfuse.prompts.with_langfuse_override("dd.synth.outline.usc_vote")
def build_usc_vote_prompt(
    *,
    candidates_summary: list[dict],
    chapter_id: str,
    chapter_title: str,
    adaptive_cap: int = 0,
) -> str:
    """USC picker prompt (Brown & Cobbe 2025): structural summaries only (not full JSON) so picker stays focused on structure and context stays small."""
    lines: list[str] = []
    for i, c in enumerate(candidates_summary):
        violations = c.get("violations") or []
        viol_str = (
            f" violations = ({len(violations)}: " + "; ".join(violations[:3]) + ")"
            if violations else " violations = (none)"
        )
        headings = c.get("headings") or []
        headings_short = ", ".join(f"{h!r}" for h in headings[:6])
        if len(headings) > 6:
            headings_short += f", ... +{len(headings) - 6} more"
        lines.append(
            f"[{i}] n_sections = {c.get('n_sections')}, "
            f"max_stage = {c.get('max_stage')}, "
            f"n_stages = {c.get('n_stages')}, "
            f"avg_prereqs = {c.get('avg_prereqs', 0.0):.2f}, "
            f"n_removed_edges = {c.get('n_removed_edges', 0)}, "
            f"avg_desc_chars = {c.get('avg_desc_chars', 0):.0f}"
            f"{viol_str}\n"
            f"     headings: {headings_short}"
        )
    candidates_block = "\n".join(lines)
    cap_clause = (
        f"AT or just under {adaptive_cap} (the adaptive cap sized to this "
        f"chapter's source pool). A candidate with MORE than {adaptive_cap} "
        f"sections is over-decomposed — its extra sections will overlap the "
        f"others and be stripped to hollow cross-references; prefer the "
        f"candidate with ~{adaptive_cap} sharply-distinct sections. Fewer "
        f"is better than more here."
        if adaptive_cap
        else "near the adaptive cap for the chapter's source pool; fewer, "
             "sharply-distinct sections beat many overlapping ones"
    )
    return (
        f"You are picking the SINGLE BEST outline for chapter "
        f"{chapter_id} ({chapter_title!r}) from {len(candidates_summary)} "
        f"candidates. Each candidate's structural summary is below "
        f"(headings + DAG shape + violation count). Pick by this "
        f"rubric, IN ORDER:\n\n"

        f"1. ZERO structural violations (banned headings, duplicate ids, "
        f"missing prereqs, deep DAG, cycles). A candidate with "
        f"violations LOSES to any candidate without — even if its "
        f"headings are better.\n"
        f"2. Section count {cap_clause}\n"
        f"3. DAG shape: prefer 2-3 stages with multiple branches over "
        f"1 stage (no deps at all — wasted scheduling info) or 4+ "
        f"stages (over-linearized).\n"
        f"4. Heading specificity: prefer concrete code-y nouns "
        f"('Async Client', 'Tool Calling') over abstract/category-y "
        f"phrases ('Core Concepts', 'Common Patterns').\n"
        f"5. Description quality (avg_desc_chars 80-250 is healthy; "
        f"<60 = too vague, >300 = run-on).\n\n"

        f"Candidates:\n{candidates_block}\n\n"
        f"Respond ONLY with valid JSON: {{\"chosen_index\": <int>}} "
        f"where the integer is 0..{len(candidates_summary) - 1}. "
        f"No prose, no explanation."
    )


@infra.langfuse.prompts.with_langfuse_override("dd.synth.outline.repair")
def build_repair_prompt(
    *,
    framework: str,
    chapter_id: str,
    chapter_title: str,
    chapter_description: str,
    current_outline_json: str,
    issues: list[str],
    sources_concat_md: str,
) -> str:
    """Repair prompt for structurally-invalid outline. LLM keeps section_ids stable where possible — downstream nodes cross-reference by id."""
    issues_block = "\n".join(f"- {x}" for x in issues)
    return (
        f"Fix structural issues in this chapter outline. Keep the SAME "
        f"JSON schema (sections only). Preserve "
        f"section_ids and headings that are already good; only change "
        f"what's needed to clear the issues below. NEVER renumber "
        f"section_ids unless you're adding a new section — downstream "
        f"nodes reference them by id.\n\n"

        f"If an issue below is a `section_id` format error: the fix is "
        f"'s' + integer ONLY (e.g. 's3') — a descriptive slug is NOT a "
        f"valid section_id no matter how well it names the topic; move "
        f"that name into `heading` instead.\n"
        f"If an issue below is a `heading` length error: pick the ONE "
        f"most central topic and name just that in 2-8 words — do NOT "
        f"comma-separate multiple topics into one long heading.\n\n"

        f"FRAMEWORK: {framework}\n"
        f"CHAPTER: {chapter_id} — {chapter_title}\n"
        f"GOAL: {chapter_description}\n\n"

        f"CURRENT OUTLINE:\n{current_outline_json}\n\n"

        f"ISSUES TO FIX:\n{issues_block}\n\n"

        f"== SOURCE MATERIAL (for context only) ==\n"
        f"{sources_concat_md}\n"
        f"== END SOURCE MATERIAL ==\n\n"

        f"Respond ONLY with valid JSON matching the original schema. "
        f"NO commentary, NO markdown wrapping."
    )
