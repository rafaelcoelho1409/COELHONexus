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
# Scope Gate — classifies the framework argument before any expensive work
# =============================================================================
SCOPE_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are a scope classifier for a code-framework learning tool. "
        "Return structured output indicating whether the input refers to a "
        "code/programming framework, library, SDK, API, CLI tool, or developer-"
        "focused technical topic — in ANY programming language.\n\n"
        "True examples: 'FastAPI', 'React', 'CUDA', 'Terraform', 'Rust tokio', "
        "'OpenTelemetry Python', 'Kubernetes', 'Docker Compose', 'TensorFlow', "
        "'Next.js', 'Django', 'pandas', 'PyTorch', 'Go gRPC'.\n\n"
        "False examples: 'how to bake a cake', 'stock market tips', "
        "'yoga for beginners', 'marketing strategy', 'cooking recipes', "
        "'self-improvement', 'relationship advice', 'history of WWII'.\n\n"
        "If True, ALSO populate docs_url with the canonical documentation "
        "root URL you are most confident about. Examples: 'pydantic' → "
        "'https://docs.pydantic.dev/latest', 'jinja2' → "
        "'https://jinja.palletsprojects.com/en/stable', 'FastAPI' → "
        "'https://fastapi.tiangolo.com', 'React' → 'https://react.dev', "
        "'tokio' → 'https://docs.rs/tokio/latest', 'OpenTelemetry Python' → "
        "'https://opentelemetry.io/docs/languages/python'. Return the DOCS "
        "root, not the project homepage; never guess if unsure — leave null.\n\n"
        "If False, populate rejection_reason with a one-line user-facing "
        "explanation (e.g., 'This tool only covers code frameworks; baking "
        "tutorials are out of scope'). If True, leave rejection_reason empty."
    ),
    ("human", "{framework}"),
])


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
        "2. Every file slug is EITHER assigned to one chapter OR listed in "
        "   `unused_files`. No file in both. Every slug from the input MUST "
        "   appear somewhere (assigned or unused).\n"
        "3. Use `unused_files` for low-value files (shard labelers already "
        "   flagged some via `unused_shard_slugs` — propagate those and add "
        "   any others you spot).\n"
        "4. Reading order must build foundations first, integrations later.\n"
        "5. Each chapter: concrete title (what it covers), one-sentence goal "
        "   (what the reader gains), assigned_files drawn from the input "
        "   clusters.\n"
        "6. Slugs MUST match the input slug list EXACTLY — do not invent "
        "   new slugs, do not modify existing ones.\n"
        "7. Do NOT inflate chapter count. Fewer, well-grouped > many fragmented."
    ),
    (
        "human",
        "Framework: {framework}\n\n"
        "Micro-clusters from {shard_count} shards:\n"
        "{cluster_summary}\n\n"
        "Shard-level unused_files candidates:\n"
        "{shard_unused}\n\n"
        "All corpus slugs (for your reference — every slug must be "
        "accounted for):\n"
        "{all_slugs}\n\n"
        "Produce the final chapter plan."
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
        "Output requirements:\n"
        "1. content — full chapter markdown. Start every section with code, not "
        "   prose. Every API call / feature mentioned gets a citation comment: "
        "   `# docs: <file_slug>` on its own line above the code. No 'In this "
        "   chapter we will learn...' intros. No 'Summary' or 'Conclusion' sections.\n"
        "2. challenges — 5-10 active-recall questions as a markdown numbered list. "
        "   Mix of conceptual ('Why does X block on Y?') and applied ('Write a "
        "   function that does Z using this framework').\n"
        "3. flashcards — 8-15 Anki-style Q/A pairs. Front = concise prompt, "
        "   back = precise answer. Each pair should stand alone.\n\n"
        "{tone_block}\n\n"
        "If previous_adjustments is non-empty, apply those corrections — the "
        "grader flagged issues on a prior attempt."
    ),
    (
        "human",
        "Framework: {framework}\n"
        "Chapter: {chapter_number} — {chapter_title}\n"
        "Goal: {chapter_goal}\n\n"
        "Assigned documentation files (raw content):\n"
        "{assigned_files_content}\n\n"
        "Previous adjustments from grader (empty on first attempt):\n"
        "{previous_adjustments}\n\n"
        "Synthesize the chapter."
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
        "Calibration anchors:\n"
        "- 1.0 signal_to_noise = every section opens with code, zero 'we will "
        "  learn' intros, context limited to 2 sentences after each code block.\n"
        "- 0.5 signal_to_noise = mixed — some code-first, some prose-heavy intros.\n"
        "- 0.0 signal_to_noise = long prose intros, multi-paragraph context before "
        "  any code.\n\n"
        "- 1.0 citation_integrity = every non-trivial claim or API call has a "
        "  `# docs: <file>` comment.\n"
        "- 0.5 citation_integrity = ~half of claims cited.\n"
        "- 0.0 citation_integrity = no citations at all.\n\n"
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
        "Chapter content to evaluate:\n"
        "{synthesis_text}\n\n"
        "Score across the 8 dimensions and emit specific issues if below threshold."
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
        "   scoring dimensions first.\n\n"
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
        "3. PRESERVE every code block verbatim. Do NOT 'improve' code style, "
        "   rename variables, reorder imports, or add comments.\n"
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
        "Return ONLY the curated chapter markdown, no preamble. Preserve "
        "facts, citations, and code blocks exactly."
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
# SearXNG candidates. Returns the canonical docs_url in strict JSON schema.
# Inspired by Context7's server-side rerank (Upstash, Jan 2026).
RESOLVER_RERANK_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You pick the canonical documentation root URL for a software framework "
        "from a list of candidates. You do NOT browse the web. You work ONLY "
        "from the evidence provided.\n\n"
        "RULES:\n"
        "1. ONLY pick a URL from the CANDIDATES list. Never invent URLs.\n"
        "2. Prefer OFFICIAL publisher sites:\n"
        "   - Vendor-owned domains (e.g. 'fastapi.tiangolo.com', "
        "     'python.langchain.com')\n"
        "   - Org GitHub Pages (e.g. 'langchain-ai.github.io/langgraph')\n"
        "   - Documentation subdomains ('docs.*.{{io,com,dev,org}}')\n"
        "3. Prefer URLs containing '/docs', '/documentation'.\n"
        "4. REJECT with a reason:\n"
        "   - PyPI / npm / crates.io / rubygems package pages (registry != docs)\n"
        "   - Reddit, HackerNews, StackOverflow, Medium, dev.to, blog posts\n"
        "   - GitHub README anchors (pick the repo root instead, or prefer a "
        "     dedicated docs site if one exists in candidates)\n"
        "   - Mirror or fork orgs with low apparent star count\n"
        "   - 'awesome-*' list pages\n"
        "5. If the framework has a dedicated docs subdomain AND a project "
        "   homepage, prefer the docs subdomain.\n"
        "6. If multiple locales exist, prefer English unless the user "
        "   specified otherwise.\n"
        "7. VERSION handling: if a version is requested, prefer URLs whose "
        "   path clearly matches (e.g. '/2.11/', '/v3/', '/stable/'). If no "
        "   exact match, fall back to the root or 'latest' variant — do NOT "
        "   reject just because the URL lacks a version path.\n"
        "8. Confidence:\n"
        "   - 0.9+: one obvious winner, clearly official\n"
        "   - 0.7-0.9: likely correct with one plausible alternative\n"
        "   - 0.4-0.7: ambiguous — multiple plausible candidates\n"
        "   - <0.4: genuine guess; caller will surface fallback_candidates\n"
        "9. Populate `rejected` with up to 5 'url:reason' entries — short "
        "   reasons (max 60 chars each) so the caller can surface them as "
        "   fallback candidates."
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
