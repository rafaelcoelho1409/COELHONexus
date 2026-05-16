"""
Knowledge Distiller — Prompt Templates

All LLM-facing system + user messages for the KD pipeline live here.
Separating prompts from logic means iterating on tone/phrasing never
touches any node or service file.

Design principle: ONE prompt, VARIABLE tone. The synthesizer prompt has
a {tone_block} placeholder that build_tone_block(user_profile) fills at
runtime. Coverage of the source material is held CONSTANT across user
levels — only presentation changes.

Reference: docs/KNOWLEDGE-DISTILLER-WHOLE-DOCS-VARIABLE-TONE.md
"""
from langchain_core.prompts import ChatPromptTemplate

from schemas.knowledge.inputs import UserProfile


# =============================================================================
# Tone Adapter — interpolated into SYNTHESIZER_PROMPT per user_profile
# =============================================================================
def build_tone_block(profile: UserProfile) -> str:
    """
    Return a plain-text block describing presentation preferences for this user.
    Coverage is never skipped; this only adjusts HOW the content is presented.
    """
    level = profile.level
    markets = ", ".join(profile.target_markets) or "general"
    mastered = ", ".join(profile.mastered_technologies[:15]) or "none declared"
    portfolio = ", ".join(profile.portfolio_refs[:10]) or "none declared"
    if level == "senior":
        density = "70%+ of non-blank lines should be code. Minimal prose. Production-focused patterns, edge cases, gotchas."
        assumptions = f"SKIP intros for: {mastered}. Assume full mastery. Jump to framework-specific novel aspects."
    elif level == "mid":
        density = "55% code. Some bridging prose. Focus on how this framework differs from mastered tech."
        assumptions = f"SKIP basic intros for: {mastered}. Briefly bridge to novel concepts."
    else:  # junior
        density = "40% code. Progressive complexity: simple example first, then real-world pattern."
        assumptions = "Explain prerequisites. Do NOT assume prior framework knowledge."
    return (
        "TONE GUIDANCE (adjust PRESENTATION only — never skip coverage):\n"
        f"- User level: {level}\n"
        f"- Code density: {density}\n"
        f"- Assumptions: {assumptions}\n"
        f"- Target markets: {markets} — weave in market hooks where naturally relevant, never forced\n"
        f"- User's portfolio projects: {portfolio} — reference in examples when genuinely applicable"
    )


# =============================================================================
# Planner — decomposes the ingested corpus into 4-12 chapters
# =============================================================================
PLANNER_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are the Planner for an adaptive code-framework study generator. "
        "Given a list of documentation files (slug + first ~200 chars of each), "
        "produce an ORDERED chapter plan that covers the MEANINGFUL parts of "
        "the corpus.\n\n"
        "Rules:\n"
        "1. 4 ≤ N ≤ 12 chapters. Small frameworks with shallow surface area use "
        "   fewer chapters; deep frameworks with rich theory use more.\n"
        "2. Every file is EITHER assigned to exactly one chapter OR listed in "
        "   `unused_files` with a brief reason. No file can appear in both.\n"
        "3. Use `unused_files` to drop LOW-VALUE material (release notes, "
        "   auto-generated API stubs with no prose, navigation pages, "
        "   duplicates, community/marketing pages that leaked through). "
        "   Target drop rate: <20% of corpus. Higher rates indicate an "
        "   ingestion problem — flag this in reasoning.\n"
        "4. Reading order must build: foundations first, integrations/advanced "
        "   later. A reader should never hit a concept before its prerequisite.\n"
        "5. Each chapter has a concrete title (what it covers) and a one-sentence "
        "   goal (what the reader gains).\n"
        "6. Do NOT inflate chapter count to 'look thorough'. Fewer, well-grouped "
        "   chapters beat many fragmented ones.\n"
        "7. Slugs MUST match the input list exactly — do not invent or modify slugs."
    ),
    (
        "human",
        "Framework: {framework}\n\n"
        "Corpus files (slug — first ~200 chars):\n"
        "{corpus_summary}\n\n"
        "Produce the chapter plan. Drop noise to unused_files."
    ),
])


# =============================================================================
# Map-Reduce Planner — shard-labeler (MAP pass)
# =============================================================================
# Used when the corpus is too large to fit in a single PLANNER_PROMPT call
# (observed threshold: ~100+ files → Groq 12K TPM 413 / NIM 504). The corpus
# is sharded into chunks of ≤40 files; this prompt labels each shard's files
# into 1-3 micro-clusters. A second REDUCE call merges all shard results
# into the final ChapterPlanList.
SHARD_LABEL_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are the Shard Labeler, one worker in a map-reduce planner "
        "pipeline. Your ONLY job: group this shard's ~40 files into 1-3 "
        "micro-clusters based on topic. A downstream reducer will merge "
        "micro-clusters from other shards into final chapters.\n\n"
        "Rules:\n"
        "1. Produce 1-3 clusters (5 hard max). Small shards with one clear "
        "   topic → 1 cluster; mixed shards → 2-3.\n"
        "2. cluster_name: 2-6 word topic label. Use CONSISTENT terminology — "
        "   e.g., 'CLI Agent Runtime' for all CLI-runtime files across shards, "
        "   'Filesystem Middleware' for all filesystem files. The reducer "
        "   relies on exact-match label substrings to merge.\n"
        "3. description: one sentence, ≤150 chars. What the cluster covers.\n"
        "4. file_slugs: only slugs from THIS shard's input list. Do NOT "
        "   invent or modify slugs.\n"
        "5. unused_shard_slugs: drop low-value noise here (auto-gen API "
        "   stubs with no prose, release notes, navigation pages, duplicates). "
        "   Target: <20% of shard.\n"
        "6. Do NOT try to name chapters. That's the reducer's job. You just "
        "   group files topically."
    ),
    (
        "human",
        "Framework: {framework}\n\n"
        "Shard files (slug — first ~80 chars):\n"
        "{shard_summary}\n\n"
        "Group this shard's files into 1-3 micro-clusters."
    ),
])


# =============================================================================
# Map-Reduce Planner — reducer (REDUCE pass)
# =============================================================================
# Takes N shard results (N = ceil(corpus_size / 40)) and merges them into
# the final ChapterPlanList. This call sees only cluster summaries — much
# smaller prompt than the single-shot PLANNER_PROMPT on a large corpus.
CHAPTER_REDUCE_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are the Chapter Reducer, the second pass of a map-reduce "
        "planner. N shard-labeler workers have grouped the corpus into "
        "micro-clusters. Your job: merge them into 4-12 ordered learning "
        "chapters for the framework.\n\n"
        "Rules:\n"
        "1. 4 ≤ N ≤ 12 chapters. Merge clusters with similar names/topics "
        "   into one chapter. Don't create a 1-to-1 shard→chapter mapping.\n"
        "2. Every file slug you assign MUST come from the micro-clusters' "
        "   file_slugs lists or from the shard-level unused_files candidates "
        "   — NEVER invent new slugs.\n"
        "3. Use `unused_files` for low-value files (shard labelers already "
        "   flagged some via `unused_shard_slugs` — propagate those and add "
        "   any others you spot as noise, e.g. API-reference stubs).\n"
        "4. Coverage is enforced AFTER reduce by a deterministic repair pass: "
        "   any slug you don't mention will be auto-parked into unused_files. "
        "   You therefore DO NOT need to enumerate every corpus slug — focus "
        "   on grouping the meaningful clusters into coherent chapters.\n"
        "5. Reading order must build foundations first, integrations later.\n"
        "6. Each chapter: concrete title (what it covers), one-sentence goal "
        "   (what the reader gains), assigned_files drawn from the input "
        "   clusters.\n"
        "7. Slugs MUST match the input file_slugs text EXACTLY — do not "
        "   invent new slugs, do not modify existing ones.\n"
        "8. Do NOT inflate chapter count. Fewer, well-grouped > many fragmented."
    ),
    (
        "human",
        "Framework: {framework}\n\n"
        "Micro-clusters from {shard_count} shards (each cluster lists its "
        "file_slugs inline):\n"
        "{cluster_summary}\n\n"
        "Shard-level unused_files candidates:\n"
        "{shard_unused}\n\n"
        "Produce the final chapter plan."
    ),
])


# =============================================================================
# Clio-pattern REDUCE — meta-cluster labeler (2026-04-22)
# =============================================================================
# Replaces the single-shot CHAPTER_REDUCE_PROMPT for large corpora (>~80
# micro-clusters). Architecture (Anthropic Clio, arxiv 2412.13678):
#   1. MAP (unchanged): N shard-labelers emit ~300 micro-clusters
#   2. Embed each micro-cluster's (name + description) with a local model
#   3. k-means groups the ~300 vectors into M meta-clusters (M ∈ [4,12]
#      picked by silhouette score)
#   4. For EACH meta-cluster, this prompt emits one chapter's title + goal
#      (assigned_files is computed deterministically as the union of the
#      member micro-clusters' file_slugs — no LLM needed for that).
#   5. A separate ordering call (ORDER_PROMPT) sequences the M chapters.
#
# Why the split: the single-shot REDUCE call reliably hits NIM's 300s
# gateway timeout and Groq's 12K TPM cap at 300 clusters. Each META_LABEL
# call sees ~30 micro-clusters (~3K tokens) — safely under every constraint.
META_LABEL_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are the Chapter Labeler. You see N micro-clusters that share "
        "a theme (grouped by semantic similarity of their name + description). "
        "Your job: emit ONE chapter for this meta-cluster — a concrete title "
        "and a one-sentence goal.\n\n"
        "Rules:\n"
        "1. title: 2-6 words. Describe what the chapter covers (the "
        "   intersection of the input clusters' topics). Examples: "
        "   'State Management & Reducers', 'Streaming & Async Runtimes', "
        "   'Agent Middleware'. AVOID generic titles like 'Overview' or "
        "   'Miscellaneous'.\n"
        "2. goal: one sentence, ≤200 chars. What the reader gains from "
        "   this chapter (not what it contains). Start with a verb: "
        "   'Understand...', 'Learn to...', 'Build...'.\n"
        "3. You do NOT assign file_slugs — they're computed automatically "
        "   as the union of the input micro-clusters. Do not enumerate them.\n"
        "4. You do NOT pick chapter number — the ordering pass does that.\n"
        "5. If the micro-clusters seem incoherent (a stray noise cluster "
        "   that k-means grouped incorrectly), still emit the best title "
        "   you can. The critic will flag it downstream."
    ),
    (
        "human",
        "Framework: {framework}\n\n"
        "Meta-cluster ID: {meta_id}\n"
        "Member micro-clusters ({n_members}):\n"
        "{member_lines}\n\n"
        "Emit the chapter title + goal."
    ),
])


# =============================================================================
# Clio-pattern REDUCE — chapter ordering pass
# =============================================================================
# Single small call that receives M chapter (title, goal) pairs and returns
# the reading order as a list of indices. ~2K tokens — safe on every model.
# Prerequisites-first pedagogy: foundations before integrations/advanced.
ORDER_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are the Reading-Order Planner. You see M unordered chapter "
        "drafts for a framework's study material. Your job: return the "
        "best reading order as a list of 0-indexed chapter indices.\n\n"
        "Rules:\n"
        "1. Foundations first (setup, core concepts, state primitives), "
        "   then integrations (external services, middleware), then "
        "   advanced (custom runtime, orchestration, internals).\n"
        "2. A reader should NEVER hit a concept before its prerequisite. "
        "   Cross-chapter prerequisites dominate over topical grouping.\n"
        "3. Emit EXACTLY M indices, one permutation of 0..M-1. No repeats. "
        "   No missing indices.\n"
        "4. rationale: one sentence explaining the spine of the ordering.\n"
        "5. If two chapters are truly parallel (no prerequisite between "
        "   them), place the simpler one first."
    ),
    (
        "human",
        "Framework: {framework}\n\n"
        "Chapter drafts (index: title — goal):\n"
        "{chapter_lines}\n\n"
        "Return the reading order."
    ),
])


# =============================================================================
# Synthesizer — generates the chapter README + challenges + flashcards
# =============================================================================
SYNTHESIZER_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are the Chapter Synthesizer for an adaptive code-framework study "
        "generator. You receive raw documentation files and produce a chapter "
        "that COMPRESSES the material (~4:1 ratio: 400 pages of docs → ~100 pages "
        "of study). The compression comes from removing redundancy, code-first "
        "presentation, smart cross-references, and stripping ceremony — NOT from "
        "dropping coverage.\n\n"
        "Output shape (STRUCTURED — Tier 3 #21):\n"
        "1. sections — ordered list of Section objects. Each Section has:\n"
        "     - heading: section title WITHOUT leading '#' markers "
        "(assembler adds the right level). 2-8 words, concrete, code-y.\n"
        "     - prose_md: body markdown. MUST NOT contain triple-backtick "
        "(```) code fences — put code in `code_refs`, the assembler "
        "interleaves it. Inline `code` spans (single backtick) are fine. "
        "Include `# docs: <file_slug>` citations on their own lines.\n"
        "     - code_refs: ordered list of 12-hex-char hashes. Take each "
        "hash from the input's `<code-ref hash=\"<12-hex>\"/>` tags — "
        "put ONLY the bare 12-hex value (no `<`, no `hash=`, no quotes). "
        "The assembler emits each referenced code block AFTER prose_md in "
        "the order you list.\n"
        "     DISTRIBUTION RULES (batch-3, 2026-04-23):\n"
        "       * Every vault hash MUST appear in EXACTLY ONE section's "
        "code_refs — NEVER in two sections. Duplicate hashes cause the "
        "same block to be rendered repeatedly in unrelated sections. "
        "Pick the section where the code is topically most relevant.\n"
        "       * Every section SHOULD have at least one code_ref. A "
        "section with substantive prose but empty code_refs is a "
        "distribution failure (either add a relevant hash, merge the "
        "prose into a code-bearing section, or shorten to a ≤40-char "
        "transition line).\n"
        "       * Every vault hash in the input MUST appear exactly once "
        "across the union of all sections' code_refs. Missing hashes, "
        "invented hashes, or duplicated hashes all fail the audit and "
        "force a refine retry.\n"
        "2. challenges — 5-10 active-recall questions as a markdown numbered list. "
        "   Mix of conceptual ('Why does X block on Y?') and applied ('Write a "
        "   function that does Z using this framework').\n"
        "3. flashcards — 8-15 Anki-style Q/A pairs. Front = concise prompt, "
        "   back = precise answer. Each pair should stand alone.\n\n"
        "PROSE-DENSITY RULES (OP-38 + OP-40, 2026-04-25):\n"
        "  - The FIRST section of the chapter MUST open with 2-3 sentences of "
        "ORIENTATION before any code block: what the reader will learn, why "
        "it matters, what prerequisites are assumed. This is non-negotiable "
        "for cold-read entry.\n"
        "  - For EVERY code_ref a section places, write 2-3 sentences of "
        "explanation BEFORE that code in prose_md: what the snippet does, "
        "when to use it, the non-obvious parameter or return value. Don't "
        "pad — each sentence must add information the reader cannot see "
        "from the code alone.\n"
        "  - A section with N code_refs needs roughly 2N-3N concrete "
        "sentences of teaching prose. Sections with code-dump shape "
        "(many refs, few words) fail the audit and force a refine.\n\n"
        "{tone_block}\n\n"
        "If previous_adjustments is non-empty, apply those corrections — the "
        "audit or grader flagged issues on a prior attempt."
    ),
    (
        "human",
        "Framework: {framework}\n"
        "Chapter: {chapter_number} — {chapter_title}\n"
        "Goal: {chapter_goal}\n\n"
        "Assigned documentation files (contain <code-ref> placeholders):\n"
        "{assigned_files_content}\n\n"
        "Previous adjustments from grader (empty on first attempt):\n"
        "{previous_adjustments}\n\n"
        "CRITICAL — Structured code references:\n"
        "The documentation above contains self-closing XML tags of shape "
        "`<code-ref hash=\"abc123def456\"/>` (12 hex chars, self-closing). "
        "Each tag stands in for a source code block that will be "
        "deterministically substituted back into the assembled chapter.\n"
        "  - NEVER write code inside `prose_md`. Free-form ``` fences are "
        "forbidden. Use `code_refs` to point at the vault hashes instead.\n"
        "  - NEVER copy the `<code-ref hash=\"...\"/>` tag into `prose_md`. "
        "Put the bare 12-hex hash value into the right section's "
        "`code_refs` list.\n"
        "  - EVERY hash present in the input MUST appear in some section's "
        "`code_refs`. Missing any hash fails the chapter and forces a retry.\n"
        "  - Do not invent hashes — only use hashes that appeared inside "
        "`<code-ref hash=\"...\"/>` tags in the input.\n\n"
        "Worked example:\n"
        "  INPUT block:\n"
        "    ## Async Client\n"
        "    Use the async FastAPI test client:\n"
        "    <code-ref hash=\"abc123def456\"/>\n"
        "  CORRECT OUTPUT Section:\n"
        "    heading:  'Async Client'\n"
        "    prose_md: 'Use the async FastAPI test client. It supports "
        "`async with` contexts and shares the app's dependency overrides.'\n"
        "    code_refs: ['abc123def456']\n"
        "  Notice: the hash lives ONLY in `code_refs`; prose_md describes "
        "the code but does not contain the ``` fence or the XML tag.\n\n"
        "Self-verify before returning: count the `<code-ref hash=\"...\"/>` "
        "tags in the Assigned documentation files block above. That count "
        "MUST equal the total number of entries across ALL your sections' "
        "`code_refs` lists. If lower, add the missing hashes to the sections "
        "where the code logically belongs before returning.\n\n"
        "Synthesize the chapter."
    ),
])


# =============================================================================
# OP-46 (2026-04-25, post-Run-12) — prose-only synthesizer prompt
# =============================================================================
# Used when the chapter's source files contain ZERO fenced code blocks
# (security policies, compliance docs, design philosophy, best-practices
# narratives). Run-12 evidence: Docker chapters had code_vault={} → the
# regular synth prompt forced the LLM to invent code_refs from nothing,
# which cascaded to None across all healthy models. This variant skips
# all hash-related instructions and audits.
SYNTHESIZER_PROSE_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are the Chapter Synthesizer, prose-only variant. The source "
        "documentation for this chapter contains NO fenced code blocks — "
        "this is a prose-heavy chapter (security policy, compliance, design "
        "philosophy, organizational practice, etc.). Your output is a "
        "structured ProseChapterOutput with sections, challenges, and "
        "flashcards. NO code_refs field. NO ``` fenced code blocks.\n\n"
        "Output shape:\n"
        "1. sections — ordered list of ProseSection objects. Each Section has:\n"
        "     - heading: 2-8 words, concrete, no leading '#' markers.\n"
        "     - prose_md: body markdown. NO triple-backtick (```) fenced "
        "code blocks (the source had none). Inline `code` spans (single "
        "backtick) for short identifiers are fine. Include `# docs: "
        "<file_slug>` citations on their own lines for every non-trivial "
        "claim.\n"
        "2. challenges — 5-10 active-recall questions; mix conceptual and "
        "applied where the domain allows.\n"
        "3. flashcards — 4-15 Anki Q/A pairs.\n\n"
        "PROSE-DENSITY RULES:\n"
        "  - First section opens with 2-3 sentences of orientation: what "
        "the reader will learn, why it matters, prerequisites.\n"
        "  - Every substantive claim cites its source with `# docs: <slug>`.\n"
        "  - Dense, production-focused phrasing. Concrete > abstract. "
        "Specific examples > generic principles.\n\n"
        "{tone_block}\n\n"
        "If previous_adjustments is non-empty, apply those corrections."
    ),
    (
        "human",
        "Framework: {framework}\n"
        "Chapter: {chapter_number} — {chapter_title}\n"
        "Goal: {chapter_goal}\n\n"
        "Assigned documentation files (prose-only, no fenced code):\n"
        "{assigned_files_content}\n\n"
        "Previous adjustments (empty on first attempt):\n"
        "{previous_adjustments}\n\n"
        "Synthesize the chapter as ProseChapterOutput."
    ),
])


# =============================================================================
# OP-HIERARCHICAL-SYNTH (2026-04-26, Round 2) — Phase A outline prompt
# =============================================================================
# Generates a ChapterOutline (sections + challenges + flashcards) BEFORE any
# code is placed. No enum constraint, no code_refs field — pure prose
# generation. Eliminates the "constraint-vs-prose attention competition"
# that monolithic synth suffers on chapters with vault > 50 hashes
# (Run-16/Run-20 evidence: ch01=183, ch03=91, ch04=68 hashes all DEBT'd
# despite the existing OP-7/OP-11/OP-12/OP-31/OP-33 mitigations).
#
# Per-section vault hash assignment happens in Phase B (deterministic,
# no LLM). The LLM here only decides:
#   1. How to decompose the chapter into 4-15 sections (heading + goal)
#   2. What each section assumes from prior sections (cross-section contract)
#   3. What challenges + flashcards summarize the chapter as a whole
OUTLINE_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are the Chapter Outliner — Phase A of the hierarchical chapter "
        "synthesizer. Your job is to PRE-DECOMPOSE the chapter into 4-15 "
        "sections WITHOUT writing any prose body or placing any code blocks. "
        "A separate per-section synthesizer (Phase C) will fill each section "
        "in parallel, using the assigned vault hashes that Phase B (a "
        "deterministic router) computes from your section headings + goals.\n\n"
        "Why this split exists:\n"
        "Monolithic chapter synth on large vaults (>50 hashes) reliably "
        "oscillates between 'thin sections' (too few words per code block) "
        "and 'missing hashes' (forgot to reference half the vault) because "
        "constrained decoding pulls the model toward enum tokens at the "
        "expense of prose density. Splitting outline → routing → per-section "
        "synth keeps each constrained-decode call's enum well below the "
        "30-distractor reliability cliff (Chroma context-rot 2024).\n\n"
        "Output shape — ChapterOutline:\n"
        "1. sections — ordered list of 4-15 OutlineSection objects:\n"
        "     - heading: 2-8 words, concrete, code-y. Examples: 'Async "
        "Client', 'Dependency Injection'. Avoid 'Introduction', 'Overview', "
        "'Summary', 'Conclusion'.\n"
        "     - goal: 1-line description of what this section will teach. "
        "Phase B uses this string (with the heading) as the embedding "
        "target for routing vault hashes — be SPECIFIC about what code "
        "concepts belong here. Examples: 'how to wire DI overrides for "
        "tests' or 'the streaming response shape for tool calls'.\n"
        "     - assumes_from_prior_sections: empty for the FIRST section. "
        "For later sections, name what the reader has already absorbed in "
        "PRIOR sections of THIS chapter. Examples: 'reader knows the basic "
        "agent loop from section 1' or 'reader has seen the streaming "
        "response shape'. Used by Phase C to maintain narrative flow.\n"
        "2. challenges — 5-10 active-recall questions (markdown numbered "
        "list). Mix conceptual + applied.\n"
        "3. flashcards — 4-15 Anki Q/A pairs, each stand-alone.\n\n"
        "DECOMPOSITION RULES:\n"
        "  - Aim for sections that each cover ~5-15 vault hashes (you can "
        "estimate by skimming the source for natural topical clusters: "
        "fenced blocks under the same heading, blocks demonstrating one "
        "API surface, blocks for one configuration concern, etc.).\n"
        "  - DON'T create 'Setup' / 'Examples' / 'Reference' meta-sections. "
        "Each section should be a TOPIC, not a content type.\n"
        "  - DON'T duplicate topics across sections — Phase B routing assigns "
        "each vault hash to exactly ONE section. Overlapping headings will "
        "force the router to pick arbitrarily.\n"
        "  - The reader reads sections in order. Earlier sections should "
        "introduce concepts that later sections build on (signaled via "
        "`assumes_from_prior_sections`).\n\n"
        "{tone_block}"
    ),
    (
        "human",
        "Framework: {framework}\n"
        "Chapter: {chapter_number} — {chapter_title}\n"
        "Chapter goal: {chapter_goal}\n"
        "Estimated vault size: {n_vault_hashes} code blocks across "
        "{n_assigned_files} source files\n\n"
        "Source documentation (with `<code-ref hash=\"<12-hex>\"/>` placeholders "
        "marking where code blocks live — do NOT reference any hash here, "
        "Phase B routes them deterministically):\n"
        "{assigned_files_content}\n\n"
        "Produce a ChapterOutline with 4-15 sections that decomposes this "
        "material. Each section's `goal` should be specific enough that a "
        "deterministic router can match vault hashes to it by topical "
        "embedding similarity. Also produce challenges + flashcards that "
        "summarize the WHOLE chapter (not any single section)."
    ),
])


# =============================================================================
# OP-HIERARCHICAL-SYNTH (2026-04-26, Round 2) — Phase C per-section synth prompt
# =============================================================================
# Synthesizes ONE section of a chapter, given:
#   - the OutlineSection (heading + goal + cross-section contract)
#   - a small whitelist of vault hashes Phase B routed to this section
#     (typically 5-15 values; well under the 30-distractor cliff)
#   - the surrounding source content (for context, not for hash extraction)
#
# Returns a regular `Section` (heading + prose_md + code_refs) — Phase D
# concatenates section drafts into a ChapterOutput for the existing
# assembler / grader / critic / curator pipeline.
#
# Per-section enum is small enough that constrained decoding doesn't
# starve prose generation — the Self-Refine pathology that Run-16/Run-20
# exposed on whole-chapter synth doesn't apply here.
SECTION_SYNTH_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are the Section Synthesizer — Phase C of the hierarchical "
        "chapter synth pipeline. You write ONE section of a chapter, given "
        "(a) the section's heading + goal from the outline, (b) what the "
        "reader already knows from prior sections, and (c) a SMALL "
        "WHITELIST of vault code hashes that the deterministic router "
        "(Phase B) has assigned to this section.\n\n"
        "Output shape — Section:\n"
        "  - heading: copy the outline-provided heading EXACTLY. Do not "
        "reword it. Do not add '#' markers.\n"
        "  - prose_md: body markdown. RULES:\n"
        "      * NO triple-backtick (```) fenced code blocks — put code in "
        "`code_refs`, the assembler interleaves it.\n"
        "      * NO <code-ref hash=\"...\"/> XML tags — copy the bare "
        "12-hex hash value INTO `code_refs` instead.\n"
        "      * Include `# docs: <file_slug>` citations on their own "
        "lines for every non-trivial claim.\n"
        "      * Inline `code` spans (single backtick) are fine.\n"
        "      * Dense, production-focused, code-first phrasing.\n"
        "  - code_refs: ordered list of 12-hex-char vault hashes. ONLY "
        "values from the WHITELIST below are valid. Listing a hash NOT "
        "in the whitelist is a hard violation that fails the audit.\n\n"
        "PROSE-DENSITY RULES (OP-38 + OP-40):\n"
        "  - {orientation_clause}\n"
        "  - For EVERY code_ref this section places, write 2-3 sentences "
        "of explanation BEFORE that code in prose_md: what the snippet "
        "does, when to use it, the non-obvious parameter or return value. "
        "Don't pad — each sentence must add information the reader "
        "cannot see from the code alone.\n"
        "  - A section with N code_refs needs roughly 2N-3N concrete "
        "sentences of teaching prose. Code-dump shape (many refs, few "
        "words) fails the audit.\n\n"
        "CONTEXT FROM OUTLINE:\n"
        "  - This section's goal: {section_goal}\n"
        "  - What prior sections already covered (do NOT re-explain): "
        "{assumes_from_prior_sections}\n\n"
        "{tone_block}"
    ),
    (
        "human",
        "Framework: {framework}\n"
        "Chapter: {chapter_number} — {chapter_title}\n"
        "Section heading: {section_heading}\n\n"
        "WHITELIST — the ONLY valid 12-hex hashes for this section's "
        "code_refs (Phase B router assigned these by topical proximity "
        "to your section heading + goal):\n"
        "{valid_hashes_csv}\n\n"
        "Source documentation (use for prose context — but emit hashes "
        "ONLY from the whitelist above; vault hashes outside the "
        "whitelist were assigned to OTHER sections and will appear there):\n"
        "{assigned_files_content}\n\n"
        "Self-verify before returning:\n"
        "  1. Every entry in code_refs is in the WHITELIST above (exact "
        "string match on the 12-hex value, no `lf_`/`<`/`\"` wrappers).\n"
        "  2. Every WHITELIST hash that conceptually belongs in this "
        "section appears in code_refs (it's OK to omit a whitelist hash "
        "if it truly doesn't fit, but Phase B picked these because they "
        "DO fit — be skeptical of omissions).\n"
        "  3. Per-code-ref prose density: 2-3 explanatory sentences "
        "BEFORE each code reference.\n\n"
        "Synthesize this one section."
    ),
])


# =============================================================================
# Adaptive Grader — 8-dimensional evaluation of one synthesized chapter
# =============================================================================
GRADER_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are the Adaptive Grader. Score a synthesized chapter across 8 "
        "dimensions (0.0-1.0 each), produce a weighted composite, and emit "
        "specific actionable issues if the composite < acceptance_threshold.\n\n"
        "Weighting recommendation: signal_to_noise and citation_integrity carry "
        "double weight; others equal. Composite = weighted average.\n\n"
        "Calibration anchors — IMPORTANT structural note:\n"
        "Chapters are assembled as `## Heading` + 1-2 sentences of prose + "
        "fenced code block(s), repeated per section (Tier 3 #21 structured "
        "output). A section opens with HEADING + brief prose that introduces "
        "the code that follows — this is the correct shape; do NOT penalize "
        "it as 'prose-heavy intros'.\n"
        "- 1.0 signal_to_noise = every section: heading → ≤2 sentences of "
        "  code-introducing prose → fenced code → optional ≤2 sentences of "
        "  follow-up. Zero 'In this chapter we will learn...' meta-framing. "
        "  Zero 'Summary' / 'Conclusion' sections.\n"
        "- 0.5 signal_to_noise = mixed — some sections have tight 1-2 "
        "  sentence intros; others have 3+ paragraphs of prose before any "
        "  code, or wandering preambles.\n"
        "- 0.0 signal_to_noise = long prose intros at the chapter top, "
        "  multi-paragraph narratives between code blocks, meta-framing "
        "  throughout.\n\n"
        "- 1.0 citation_integrity = every non-trivial claim or API call has "
        "  a `# docs: <file>` comment on its own line in prose.\n"
        "- 0.5 citation_integrity = ~half of claims cited.\n"
        "- 0.0 citation_integrity = no citations at all.\n\n"
        "- code_preservation_ratio (Tier 2 #19, 2026-04-23): deterministic, "
        "  carries 2× weight alongside signal_to_noise + citation_integrity.\n"
        "  * 1.0 = every source code block appears exactly once in the "
        "    chapter, placed next to prose that introduces it.\n"
        "  * 0.5 = any of: some code blocks duplicated across sections; "
        "    OR code clumped at chapter end with prose-only sections above; "
        "    OR a section has substantive prose but no code.\n"
        "  * 0.0 = mass duplication (same block rendered 3+ times) or "
        "    missing code blocks (any source fence absent from output).\n"
        "  Rate this dimension by visual inspection of the chapter shape: "
        "  scroll top-to-bottom and ask 'does every code block appear in "
        "  the right spot, and only there?'\n\n"
        "action:\n"
        "- 'accept' if composite >= acceptance_threshold.\n"
        "- 'refine' if composite < threshold but ≥ 0.60 AND issues are localized "
        "  (specific sections to fix).\n"
        "- 'regenerate' if composite < 0.60 OR the chapter has structural problems "
        "  (wrong scope, missed files).\n\n"
        "specific_issues — CRITICAL FORMAT (span-anchored):\n"
        "When composite < acceptance_threshold, emit 3-10 issues. Each issue "
        "MUST include:\n"
        "  - span_quote: exact text quoted verbatim from the chapter "
        "    (10-200 chars). Pick a span that pinpoints the problem.\n"
        "  - dimension: the specific rubric dim this span hurts "
        "    (signal_to_noise / citation_integrity / code_density / etc.)\n"
        "  - suggestion: the exact edit to apply to ONLY this span "
        "    (≤120 chars). Avoid 'improve' / 'make better' — be surgical.\n"
        "Example:\n"
        "  span_quote: 'In this chapter, we will explore...'\n"
        "  dimension: 'signal_to_noise'\n"
        "  suggestion: 'Delete this intro sentence; open with code instead.'\n"
        "Span-anchored issues let the refiner edit narrowly without "
        "rewriting the whole chapter (CRITIC pattern, arxiv 2305.11738)."
    ),
    (
        "human",
        "Framework: {framework}\n"
        "User profile: {user_profile_summary}\n"
        "Acceptance threshold: {acceptance_threshold}\n\n"
        "Assigned files this chapter should cover:\n"
        "{assigned_files_list}\n\n"
        "Pre-grader audit signals (deterministic, OP-17 2026-04-25):\n"
        "{audit_summary}\n\n"
        "Chapter content to evaluate:\n"
        "{synthesis_text}\n\n"
        "Score across the 8 dimensions and emit specific issues if below "
        "threshold. The audit signals above are deterministic facts about "
        "this exact chapter — use them to calibrate code_preservation_ratio "
        "and citation_integrity rather than re-deriving them by inspection."
    ),
])


# =============================================================================
# Adjustment Generator — converts grader issues to actionable retry instructions
# =============================================================================
# Used as a plain text block (not a with_structured_output call) — the output
# is interpolated directly into SYNTHESIZER_PROMPT's {previous_adjustments} slot
# on the next iteration of the Self-Refine loop.
ADJUSTMENT_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are the Adjustment Generator. The grader produced span-anchored "
        "issues: each issue pins a specific text span and a surgical "
        "suggestion. Your job: turn this list into a compact markdown bullet "
        "list the Synthesizer will follow on the next attempt.\n\n"
        "Rules:\n"
        "1. Each bullet addresses ONE issue. Format:\n"
        "   - **[dimension]** Find: \"span_quote\" → Apply: suggestion\n"
        "2. Do NOT invent new issues. Only use what the grader reported.\n"
        "3. Do NOT ask for global rewrites. Keep each edit narrow to the "
        "   quoted span (CRITIC pattern — prevents over-correction).\n"
        "4. Prioritize issues whose dimension has the lowest score.\n"
        "5. Max 10 bullets. If the grader emitted more, take the lowest-"
        "   scoring dimensions first.\n"
        "6. The synthesis_text may contain `<code-ref hash=\"...\"/>` "
        "   self-closing XML tags (opaque code-block placeholders). "
        "   Treat these as literal markers, NOT as content to "
        "   critique. Never generate adjustments that target, rewrite, "
        "   or reference the contents of a <code-ref> tag — the "
        "   synthesizer cannot edit them and must reproduce them "
        "   verbatim on retry.\n\n"
        "Output: plain markdown bullet list. No preamble, no explanation."
    ),
    (
        "human",
        "Grader evaluation (span-anchored issues included):\n{evaluation_json}\n\n"
        "Chapter content that was graded (for context only — do not rewrite "
        "anything outside the span quotes):\n{synthesis_text}\n\n"
        "Produce the adjustment instructions."
    ),
])


# =============================================================================
# Critic — RAGAS-style verification after all chapters are accepted
# =============================================================================
CRITIC_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are the Post-Synthesis Critic. After all N chapters have been "
        "accepted by the Adaptive Grader, verify the study as a whole against "
        "the source corpus. Distinct from the grader: the grader scored "
        "PRESENTATION quality; you score FAITHFULNESS and CITATION VALIDITY.\n\n"
        "Score three dimensions (0.0-1.0 each):\n"
        "- citation_coverage: fraction of `# docs: <file>` references that "
        "  resolve to an actual file in the provided file_slugs list.\n"
        "- faithfulness: sample ~10 factual claims from the chapters; how many "
        "  are verifiable against their cited source content?\n"
        "- code_syntax_valid: how many code blocks appear syntactically well-formed "
        "  in their declared language (Python, JS, Rust, etc.)?\n\n"
        "overall_score = weighted composite. Target >= 0.85.\n\n"
        "Populate issues with concrete problems for DEBT.md. Format: "
        "'chapterNN:Lxx: <problem description>'."
    ),
    (
        "human",
        "Framework: {framework}\n\n"
        "Available source file slugs: {file_slugs}\n\n"
        "All chapter contents concatenated:\n{chapter_bundles}\n\n"
        "Evaluate the study."
    ),
])


# =============================================================================
# Assembler — writes summary.md (final index + reading plan)
# =============================================================================
ASSEMBLER_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are the Assembler. Given all accepted chapters (titles + goals + "
        "first ~500 chars of content), produce the study's summary.md — the "
        "first thing the reader opens.\n\n"
        "Structure:\n"
        "1. One-paragraph framing (why this study exists, what it covers, who "
        "   it's for based on user_profile.level).\n"
        "2. Reading plan: ordered list of `chapterNN/README.md` links with a "
        "   one-line takeaway per chapter.\n"
        "3. Market roadmap: if user_profile.target_markets is non-empty, a brief "
        "   section on how to leverage the framework in those markets.\n"
        "4. Money projects: 3-5 concrete, monetizable project ideas that use "
        "   this framework, aligned with user_profile.target_markets and "
        "   portfolio_refs.\n\n"
        "Keep it dense. No padding. Code-over-prose where applicable."
    ),
    (
        "human",
        "Framework: {framework}\n"
        "User profile: {user_profile_summary}\n\n"
        "Chapter index (number, title, goal, first 500 chars):\n"
        "{chapter_summaries}\n\n"
        "Produce summary.md content."
    ),
])


# =============================================================================
# Curator — style-normalizes all chapters at end of synthesis, ONE model
# =============================================================================
# Run AFTER all chapters are synthesized but BEFORE the critic so the critic
# judges the final, tone-normalized text. Different chapters may have been
# synthesized by different models in the fallback chain (one timed out, one
# rate-limited, etc.), causing voice drift. The curator makes a single pass
# over one chapter at a time, rewriting ONLY for style consistency — facts,
# citations, and code blocks are preserved verbatim.
#
# Research basis: Mixture-of-Agents (arXiv 2406.04692) — an aggregator model
# applied to heterogeneous proposer outputs reliably improves the final quality.
# HMS Analytical Software multi-agent doc pattern: a final "Holistic Agent"
# smooths transitions, aligns tone/style, resolves inconsistencies between
# sections written in isolation.
CURATOR_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are the Style Curator for a multi-chapter code-framework study. "
        "Multiple chapters were drafted by different LLMs under the same plan; "
        "your job is to NORMALIZE style across the chapters WITHOUT changing "
        "the technical content. This chapter will be rewritten once; the "
        "output replaces the input at the same MinIO key.\n\n"
        "STRICT RULES:\n"
        "1. PRESERVE every factual claim, API name, version number, and "
        "   behavior description exactly as in the original.\n"
        "2. PRESERVE every `# docs:` citation line. Do NOT remove, rename, "
        "   or rearrange citations.\n"
        "3. PRESERVE every `<code-ref hash=\"...\"/>` self-closing XML "
        "   tag (opaque placeholder for a source code block) "
        "   BYTE-EXACTLY. Do NOT modify the hash value, expand the tag, "
        "   replace it with actual code, or remove it. If the chapter "
        "   also contains raw fenced code blocks, preserve those "
        "   verbatim too — do NOT 'improve' code style, rename "
        "   variables, reorder imports, or add comments. Both "
        "   <code-ref> tags and raw fences are off-limits to style "
        "   rewriting.\n"
        "4. PRESERVE the list of flashcards and challenges exactly — they're "
        "   not passed here; work only on the chapter's main README content.\n\n"
        "WHAT TO NORMALIZE:\n"
        "a. Heading conventions — the study uses `##` for sections and `###` "
        "   for subsections. No deeper nesting except inside code-block context.\n"
        "b. Prose tone — production-focused, terse, no 'In this chapter we "
        "   will learn...' or similar meta framing.\n"
        "c. Terminology — use the GLOSSARY below consistently when the "
        "   chapter uses a synonym.\n"
        "d. Transition language — if the chapter opens with 'So,' 'Alright,' "
        "   'Let's explore,' or similar conversational warm-ups, drop them.\n"
        "e. Redundancy — if the same concept is restated in different words "
        "   within one section, keep the tightest formulation.\n\n"
        "STRUCTURAL RULES:\n"
        "- Every section opens with code, then at most 2 sentences of prose "
        "   to contextualize the code.\n"
        "- No 'Summary' or 'Conclusion' sections.\n"
        "- Preserve h1 title at top unchanged."
    ),
    (
        "human",
        "Chapter number: {chapter_number}\n"
        "Framework: {framework}\n"
        "Tone profile: {tone_block}\n\n"
        "Glossary (study-wide canonical terms):\n"
        "{glossary}\n\n"
        "=== ORIGINAL CHAPTER CONTENT ===\n"
        "{chapter_content}\n"
        "=== END ORIGINAL ===\n\n"
        "CRITICAL — Code-block preservation:\n"
        "The original content above contains self-closing XML tags of "
        "shape `<code-ref hash=\"abc123def456\"/>` (12-hex-char `hash` "
        "attribute). Each tag is an OPAQUE PLACEHOLDER for a source "
        "code block. Reproduce every <code-ref> tag BYTE-EXACTLY in "
        "your curated output. Do NOT modify the hash value, expand the "
        "tag, replace it with actual code, or remove it. If you see "
        "any raw fenced code blocks instead of tags, preserve those "
        "verbatim too. Both <code-ref> tags and raw fences are "
        "off-limits.\n\n"
        "Example of correct preservation:\n"
        "  INPUT:  Use the async client: "
        "<code-ref hash=\"abc123def456\"/>\n"
        "  OUTPUT: \"## Async Client\\n\\n"
        "<code-ref hash=\"abc123def456\"/>\\n\\n"
        "The async client lets you...\"\n"
        "  Notice: the <code-ref> tag appears verbatim in the output — "
        "NEVER modified, expanded, or removed.\n\n"
        "Self-verify before returning: count the <code-ref> tags in "
        "your curated output. That count MUST equal the number of "
        "<code-ref> tags in the ORIGINAL CHAPTER CONTENT block above. "
        "If lower, rewrite your output to include every missing tag "
        "at its logical position before returning.\n\n"
        "Return ONLY the curated chapter markdown, no preamble. "
        "Preserve facts, citations, and <code-ref> tags (or raw code "
        "blocks) exactly."
    ),
])


# =============================================================================
# Resolver — crossover decomposition (single LLM call, strict JSON schema)
# =============================================================================
# Precedent: Perplexity / Google AI Mode query fan-out. Cheap pre-pass that
# splits inputs like "Grafana Alloy + LGTM + PromQL + LogQL + River" into
# canonical topics. Canonicalization normalizes aliases so downstream resolver
# fan-out doesn't double-count the same framework.
RESOLVER_DECOMPOSE_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You decompose a combined-study request into canonical technologies "
        "the resolver can look up independently.\n\n"
        "Rules:\n"
        "1. If the input names a SINGLE framework/tool (no '+' / ',' / 'and'/'with' "
        "   joining distinct technologies), return is_crossover=false and one "
        "   topic. Example: 'FastAPI' → 1 topic.\n"
        "2. If the input joins ≥2 technologies, return is_crossover=true with "
        "   one topic per distinct canonical name.\n"
        "3. CANONICALIZE aliases so query languages collapse into their parent "
        "   product. Always apply these: 'LogQL' → 'Loki', 'PromQL' → "
        "   'Prometheus', 'River' or 'River DSL' → 'Grafana Alloy', 'PySpark' "
        "   → 'Apache Spark', 'LGTM' → ['Loki', 'Grafana', 'Tempo', 'Mimir'] "
        "   (four topics — LGTM is the stack name, not one product). Apply "
        "   analogous collapses for any other query language / sublanguage.\n"
        "4. Return at most 10 topics.\n"
        "5. Preserve the user's spelling in `topic`; put the canonicalized "
        "   form in `canonical_name`. Populate `reason` when the mapping "
        "   isn't obvious (e.g., 'PromQL is Prometheus's query language').\n"
        "6. Do NOT invent technologies the user didn't mention. Only "
        "   decompose what's there."
    ),
    (
        "human",
        "REQUEST: {framework}\n"
        "ALIASES: {aliases}\n\n"
        "Decompose into canonical topics."
    ),
])


# =============================================================================
# Resolver — LLM rerank (Stage C)
# =============================================================================
# Consumes: framework name + aliases + version hint + registry homepage/repo +
# search candidates (Exa / Tavily / Jina). Returns the canonical docs_url in
# strict JSON schema. Inspired by Context7's server-side rerank (Upstash,
# Jan 2026).
RESOLVER_RERANK_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You pick the canonical documentation root URL for a software framework "
        "from a list of candidates. You do NOT browse the web. You work ONLY "
        "from the evidence provided.\n\n"
        "RULES:\n"
        "1. ONLY pick a URL from the CANDIDATES list. Never invent URLs.\n"
        "2. Prefer OFFICIAL publisher sites, in this order:\n"
        "   a. A `docs.{{domain}}` subdomain (e.g. 'docs.langchain.com', "
        "      'docs.pydantic.dev', 'docs.nvidia.com') — the 2024+ "
        "      consolidation pattern for multi-product docs hubs.\n"
        "   b. A vendor-owned docs root (e.g. 'fastapi.tiangolo.com', "
        "      'react.dev', 'kubernetes.io/docs').\n"
        "   c. Org GitHub Pages (e.g. 'langchain-ai.github.io/langgraph').\n"
        "3. REGISTRY HINT is AUTHORITATIVE for the canonical host. If the "
        "   REGISTRY HINT block lists a `homepage:` or `Documentation:` URL "
        "   and any CANDIDATE is on that SAME HOST, pick that candidate "
        "   (or the closest-matching path on that host). The package "
        "   publisher declares this host on PyPI / npm / crates.io as the "
        "   current docs home — trust it over third-party search rankings.\n"
        "4. Prefer URLs containing '/docs', '/documentation'.\n"
        "5. REJECT with a reason:\n"
        "   - PyPI / npm / crates.io / rubygems package pages (registry != docs)\n"
        "   - Reddit, HackerNews, StackOverflow, Medium, dev.to, blog posts\n"
        "   - GitHub README anchors (pick the repo root instead, or prefer a "
        "     dedicated docs site if one exists in candidates)\n"
        "   - Mirror or fork orgs with low apparent star count\n"
        "   - 'awesome-*' list pages\n"
        "   - LEGACY / DEPRECATED docs hosts when a newer canonical exists: "
        "     if two candidates share the same registrable domain, REJECT "
        "     a language-specific / version-specific subdomain (e.g. "
        "     'python.foo.com', 'v1.foo.com', 'old.foo.com') in favor of "
        "     the `docs.foo.com` or host-root. Publishers commonly leave "
        "     legacy hosts live as an archive — they are NOT the current "
        "     canonical.\n"
        "6. If the framework has a dedicated docs subdomain AND a project "
        "   homepage, prefer the docs subdomain.\n"
        "7. If multiple locales exist, prefer English unless the user "
        "   specified otherwise.\n"
        "8. VERSION handling: if a version is requested, prefer URLs whose "
        "   path clearly matches (e.g. '/2.11/', '/v3/', '/stable/'). If no "
        "   exact match, fall back to the root or 'latest' variant — do NOT "
        "   reject just because the URL lacks a version path. When VERSION "
        "   is 'latest' (the default), prefer `/latest/` or unversioned "
        "   paths over version-pinned URLs like `/0.3/` or `/v1/`.\n"
        "9. Confidence:\n"
        "   - 0.9+: one obvious winner, clearly official\n"
        "   - 0.7-0.9: likely correct with one plausible alternative\n"
        "   - 0.4-0.7: ambiguous — multiple plausible candidates\n"
        "   - <0.4: genuine guess; caller will surface fallback_candidates\n"
        "10. Populate `rejected` with up to 5 'url:reason' entries — short "
        "    reasons (max 60 chars each) so the caller can surface them as "
        "    fallback candidates."
    ),
    (
        "human",
        "FRAMEWORK: {framework}\n"
        "ALIASES: {aliases}\n"
        "VERSION: {version}\n\n"
        "REGISTRY HINT:\n"
        "{registry_hint}\n\n"
        "CANDIDATES (from SearXNG):\n"
        "{candidates_block}\n\n"
        "Pick the canonical docs_url. Return strict JSON per schema."
    ),
])
