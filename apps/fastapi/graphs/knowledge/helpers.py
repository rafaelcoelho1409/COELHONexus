"""
Knowledge Distiller — Pipeline Helpers

Private helpers used by the node functions in graphs/knowledge/distiller.py.
Mirrors the pattern in graphs/youtube/helpers.py: nodes in the main graph
file, small focused I/O + shaping helpers here.

Layers:
  - Step 5: corpus reading + monolith split + plan validation + plan.json write
  - Step 6: chapter-file aggregation + tone/adjustment formatting +
            synthesizer/grader/adjustment LLM call wrappers + artifact writing
  - Step 7: cross-chapter reading + citation regex scan + chapter-bundle assembly

Every helper is awaitable if it touches MinIO; synchronous helpers are pure
data-shape work. Caps/thresholds defined as module constants below so they
can be tuned without hunting through function bodies.
"""
import asyncio
import json
import logging
import re
from typing import Optional

from langchain_openai import ChatOpenAI

from schemas.knowledge.agents import (
    ChapterPlan,
    ChapterPlanList,
    ChapterSynthesis,
    GraderEvaluation,
)
from schemas.knowledge.ingestion import ManifestEntry
from schemas.knowledge.inputs import UserProfile
from schemas.knowledge.prompts import (
    ADJUSTMENT_PROMPT,
    ASSEMBLER_PROMPT,
    GRADER_PROMPT,
    SYNTHESIZER_PROMPT,
)
from services.knowledge.storage import MinIOStudyStorage


logger = logging.getLogger(__name__)


# =============================================================================
# Constants (used only by helpers — nodes have their own in distiller.py)
# =============================================================================
# Preview length per file in the planner's corpus summary.
# Empirical tuning (2026-04-20):
#   - 500 chars × 499 files = 258KB → ~60K tokens → hung every NIM model at
#     its full timeout (fallback cascade stalled 15+ min).
#   - 200 chars × 499 files = 100KB → ~25K tokens → fits every model's context
#     with headroom; planner still sees heading + ~1 full sentence per file,
#     which is what it needs to cluster files into chapters.
# The preview starts at the top of each markdown file, so the first 200 chars
# typically cover: "# <Title>" + first paragraph snippet. Planner quality is
# insensitive beyond that — file grouping is driven by title more than body.
CORPUS_PREVIEW_CHARS = 80  # was 200 — reduced 2026-04-21 to keep planner
# prompt under provider token budgets on large corpora. At 994 files × 200
# chars = ~50K prompt tokens — exceeded Groq free-tier TPM (12K/min for
# llama-3.3-70b-versatile → HTTP 413) AND caused NIM upstream timeouts
# (long prompts + slow reasoning models = 504 Gateway Timeout). Lowering
# to 80 puts prompt at ~20K tokens — still above Groq TPM (so Groq will
# still skip), but NIM glm-5.1/qwen3.5-397b handle it cleanly.
#
# Proper long-term fix is the two-pass map-reduce planner
# (docs/KNOWLEDGE-DISTILLER-PLANNER-FIXES.md). 80-char preview is the
# pragmatic interim that lets single-prompt planner succeed on NIM.

# If the raw prefix contains exactly ONE object larger than this (bytes), split
# it on top-level markdown headings before planning. This handles the Tier 1
# case where /llms-full.txt arrived as a single monolithic object.
MONOLITH_SPLIT_THRESHOLD_BYTES = 50_000

# Cap on assembled raw-file content fed to the synthesizer in one call. Prevents
# blow-ups when a chapter is assigned many large pages. 40k chars ≈ 10k tokens.
CHAPTER_FILES_MAX_CHARS = 180_000

# Cap on synthesis-text length sent to the grader. The grader scores presentation
# style; it doesn't need the full chapter to do that. Keeps grader inputs cheap.
GRADER_SYNTHESIS_MAX_CHARS = 12_000

# Per-chapter cap when building the critic's chapter bundle. Prevents blowup when
# chapters are long. Critic samples faithfulness; doesn't need exhaustive input.
CRITIC_CHAPTER_MAX_CHARS = 10_000

# Overall cap on the critic's chapter_bundles. Hard ceiling for the LLM call size.
CRITIC_BUNDLE_MAX_CHARS = 50_000

# Citation pattern written by the synthesizer — see SYNTHESIZER_PROMPT in
# schemas/knowledge/prompts.py. Matches '# docs: <slug>' at any indentation.
# The captured group stops at whitespace, newline, backtick, or closing paren.
_CITATION_RE = re.compile(r"#\s*docs:\s*([^\s\n`)]+)", re.MULTILINE)

# Per-chapter preview length used by the assembler when building summary.md.
# Short enough that the whole index fits easily in one LLM call.
ASSEMBLER_PREVIEW_CHARS = 500


# =============================================================================
# Step 5 — planner helpers
# =============================================================================
async def _read_raw_prefix(
    storage: MinIOStudyStorage,
    study_root: str) -> list[tuple[str, str]]:
    """
    List all *.md objects under <study_root>/research/raw/ and read each in
    parallel via a SHARED aioboto3 client (storage.read_many) — avoids the
    per-request TLS + SigV4 handshake that serialized a prior naive parallel
    implementation through the Semaphore slots. Returns [(slug, content), ...]
    preserving sorted-by-key order.

    Raises FileNotFoundError if the prefix has no objects.
    """
    prefix = f"{study_root}/research/raw/"
    keys = await storage.list(prefix)
    md_keys = sorted(k for k in keys if k.endswith(".md"))
    if not md_keys:
        raise FileNotFoundError(f"no raw objects under {prefix!r}")
    contents = await storage.read_many(md_keys)
    return [
        (k.rsplit("/", 1)[-1].removesuffix(".md"), c)
        for k, c in zip(md_keys, contents)
    ]


async def _maybe_split_monolith(
    storage: MinIOStudyStorage,
    study_root: str,
    entries: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """
    If there's exactly ONE object and it's large, split it on H1/H2 boundaries
    that are NOT inside fenced code blocks, delete the original, write the
    per-section outputs. Idempotent: a pre-split corpus (len != 1) returns
    unchanged.

    Why: Tier 1 (/llms-full.txt) writes a single monolithic object (often
    multi-MB — the publisher's full family docs in one file). The planner
    works better with N small "pseudo-files" it can chapter-ize around
    instead of one giant blob.

    BUG FIX (2026-04-22): The previous implementation used a line-anchored
    regex `re.split(r"(?=^#{1,2}\\s+)", ..., flags=re.MULTILINE)`. That
    regex matches ANY line starting with 1-2 hashes + whitespace — which
    includes Python / Bash / YAML comment lines INSIDE fenced code blocks
    (e.g. `# 1. Add resource authorization`, `## TODO`, `# bash comment`).
    Measurement on a real LangChain-family llms-full.txt (3125 splits):
    414 output files (13%) had an unbalanced fence count — mathematical
    proof that the regex cut mid-code-block. The monolith's code was
    corrupted, later synthesizer chapters cited broken code, and the
    apparent "trafilatura is stripping code" symptom was actually THIS
    post-ingest splitter truncating fenced sections.

    New implementation uses LangChain's
    `ExperimentalMarkdownSyntaxTextSplitter`, a CommonMark-aware tokenizer
    that tracks fenced-code-block state and never treats comments inside
    fences as heading boundaries. Verified empirically: Python `# 1. foo`,
    `## TODO`, and bash `# comment` lines inside ```python / ```bash
    fences are preserved intact as part of the surrounding section.
    """
    if len(entries) != 1:
        return entries
    slug, content = entries[0]
    if len(content.encode("utf-8")) < MONOLITH_SPLIT_THRESHOLD_BYTES:
        return entries

    # Local import — the splitter module triggers a sizable dependency chain
    # that we don't want loaded at graph-build time. Only the monolith
    # path (Tier 1 with a large llms-full.txt) needs it.
    from langchain_text_splitters.markdown import (
        ExperimentalMarkdownSyntaxTextSplitter,
    )

    splitter = ExperimentalMarkdownSyntaxTextSplitter(
        headers_to_split_on = [("#", "H1"), ("##", "H2")],
        strip_headers = False,   # keep the heading text inside page_content so
                                 # the output file opens with "# Heading Name"
    )
    chunks = splitter.split_text(content)
    if len(chunks) < 3:
        logger.info(
            f"[planner] monolith {slug}.md has too few headings to split; keeping as-is"
        )
        return entries

    # Group chunks by (H1, H2) — the splitter emits one chunk per distinct
    # block (heading, prose-between-codes, fenced code, ...). Chunks sharing
    # the same (H1, H2) metadata belong to the SAME section and must be
    # concatenated in document order so code blocks land back inside their
    # surrounding section.
    grouped: list[tuple[tuple[str, str], list[str]]] = []
    current_key: tuple[str, str] | None = None
    current_parts: list[str] = []
    for ch in chunks:
        key = (ch.metadata.get("H1", ""), ch.metadata.get("H2", ""))
        if current_key is None:
            current_key = key
        if key != current_key and current_parts:
            grouped.append((current_key, current_parts))
            current_parts = []
            current_key = key
        current_parts.append(ch.page_content)
    if current_parts and current_key is not None:
        grouped.append((current_key, current_parts))

    if len(grouped) < 3:
        logger.info(
            f"[planner] monolith {slug}.md produced {len(grouped)} sections "
            f"(under minimum of 3); keeping as-is"
        )
        return entries

    prefix = f"{study_root}/research/raw/"
    # Delete the original; write each section as its own object.
    await storage.delete(f"{prefix}{slug}.md")

    # Phase 1 (pure Python, sequential — fast) — compute unique slug + body
    # for every section. Sequential ordering is REQUIRED here because slug
    # de-duplication (when two H2 "Overview" appear under different H1s)
    # depends on order-of-arrival via `used_slugs`. This pass is CPU-bound
    # at thousands of iters/sec; no I/O.
    writes: list[tuple[str, str]] = []
    used_slugs: set[str] = set()
    for i, ((h1, h2), parts) in enumerate(grouped):
        # Prefer the deepest heading (H2 > H1) for the slug; fall back to
        # a stable positional label when a group precedes any heading.
        heading_text = h2 or h1 or f"section-{i:04d}"
        sub = re.sub(r"[^a-z0-9]+", "-", heading_text.lower()).strip("-")[:60]
        if not sub:
            sub = f"section-{i:04d}"
        full_slug = sub if sub.startswith(slug) else f"{slug}-{sub}"
        # Disambiguate collisions (e.g. two H2 "Overview" under different H1s).
        candidate = full_slug
        dedup_n = 2
        while candidate in used_slugs:
            candidate = f"{full_slug}-{dedup_n}"
            dedup_n += 1
        used_slugs.add(candidate)
        writes.append((candidate, "".join(parts)))

    # Phase 2 (async, parallel via SHARED aioboto3 client) — write every
    # section through storage.write_many so the TLS + SigV4 handshake cost
    # is paid ONCE for the batch instead of per-file. Measured 2026-04-22:
    # per-call client (3700 writes, Semaphore(8)) ≈ 1h wall-clock due to
    # handshake serialization; shared-client batch at the same concurrency
    # targets ~90s. File keys are independent; the write_many internal
    # Semaphore(8) caps in-flight PUTs to the aioboto3-stable threshold.
    await storage.write_many(
        [(f"{prefix}{candidate}.md", body, "text/markdown")
         for candidate, body in writes]
    )

    logger.info(
        f"[planner] split monolith {slug}.md into {len(writes)} sections "
        f"(CommonMark tokenizer; fence-aware — code blocks preserved; "
        f"parallel MinIO writes × 32)"
    )
    return writes


def _build_corpus_summary(entries: list[tuple[str, str]]) -> str:
    """
    Produce the {corpus_summary} interpolation for PLANNER_PROMPT.
    Format: one line per file — 'slug — first ~500 chars collapsed to one line'.
    """
    lines = []
    for slug, content in entries:
        preview = content[:CORPUS_PREVIEW_CHARS].strip()
        # Collapse whitespace so each file fits one readable line in the prompt
        preview = re.sub(r"\s+", " ", preview)
        lines.append(f"{slug} — {preview}")
    return "\n".join(lines)


def _validate_plan(
    plan: ChapterPlanList,
    available_slugs: set[str]) -> list[str]:
    """
    Check the plan against the on-disk corpus. Returns a list of warnings
    (empty = plan is clean). Does NOT raise — planner logs warnings and the
    critic node catches downstream quality issues.

    A file is "accounted for" if it's either in a chapter's assigned_files
    OR in `unused_files`. Both paths are valid — `unused_files` is the
    deliberate-drop bucket for release notes / stubs / navigation pages.

    Checks:
      1. No file assigned to two chapters at once
      2. No file BOTH assigned AND in unused_files
      3. No file left unaccounted for (missing from both buckets)
      4. No hallucinated slug (LLM referencing a file that doesn't exist)
      5. Chapter numbers form a contiguous 1..N sequence
      6. `unused_files` drop rate not wildly high (>50% likely indicates a bug)
    """
    warnings: list[str] = []
    assigned: dict[str, int] = {}  # slug → chapter number
    for ch in plan.chapters:
        for slug in ch.assigned_files:
            if slug in assigned:
                warnings.append(
                    f"file {slug!r} assigned to both chapter {assigned[slug]} and {ch.number}"
                )
            assigned[slug] = ch.number

    unused_slugs = {u.slug for u in (plan.unused_files or [])}

    # File can't be both assigned AND explicitly unused
    overlap = set(assigned.keys()) & unused_slugs
    if overlap:
        sample = sorted(overlap)[:5]
        warnings.append(
            f"{len(overlap)} slugs appear in BOTH assigned_files and unused_files "
            f"(sample: {sample}); unused_files wins"
        )

    accounted = set(assigned.keys()) | unused_slugs
    missing = available_slugs - accounted
    if missing:
        sample = sorted(missing)[:5]
        warnings.append(
            f"{len(missing)} files missing from BOTH assigned and unused "
            f"(sample: {sample}) — planner must account for every file"
        )

    hallucinated = (accounted - available_slugs)
    if hallucinated:
        sample = sorted(hallucinated)[:5]
        warnings.append(
            f"{len(hallucinated)} hallucinated slugs not in research/raw/ (sample: {sample})"
        )

    numbers = sorted(ch.number for ch in plan.chapters)
    expected = list(range(1, len(numbers) + 1))
    if numbers != expected:
        warnings.append(f"chapter numbers are {numbers} (expected contiguous {expected})")

    # Drop-rate sanity check
    if available_slugs:
        drop_rate = len(unused_slugs & available_slugs) / len(available_slugs)
        if drop_rate > 0.50:
            warnings.append(
                f"drop rate is {drop_rate:.0%} of corpus — likely an ingestion "
                "problem or an over-aggressive planner; review unused_files reasons"
            )
    return warnings


def _deterministic_linter(chapters: list[tuple[int, str]]) -> list[str]:
    """
    Cheap, LLM-free quality check across all accepted chapters — runs inside
    the critic node alongside the RAGAS-style LLM judge.

    Catches style drift that the LLM critic is bad at flagging:
      - heading depth variance (one chapter all `##`, next all `####`)
      - code density outside a reasonable band per tone level
      - wildly different chapter lengths (stub vs epic)

    Returns a list of lint issues (empty = clean). Issues are added to the
    critic's `issues` field, which Assembler aggregates into DEBT.md.
    """
    import re as _re
    issues: list[str] = []
    if len(chapters) < 2:
        return issues

    # 1) Heading depth variance
    heading_depths: list[set[int]] = []
    for n, content in chapters:
        depths = {len(m.group(1)) for m in _re.finditer(r"^(#+)\s", content, _re.MULTILINE)}
        heading_depths.append(depths)
    all_depths = set().union(*heading_depths) if heading_depths else set()
    if max(all_depths, default = 0) - min(all_depths, default = 0) >= 3:
        depth_map = [f"ch{n:02d}:{sorted(d)}" for (n, _), d in zip(chapters, heading_depths)]
        issues.append(
            f"heading depth varies widely across chapters ({', '.join(depth_map[:6])}...) "
            "— curator pass should normalize"
        )

    # 2) Code density (fraction of non-blank lines that look like code)
    densities: list[tuple[int, float]] = []
    for n, content in chapters:
        lines = [ln for ln in content.splitlines() if ln.strip()]
        if not lines:
            continue
        # Heuristic: inside a ``` fence OR starts with 4-space indent
        in_fence = False
        code_lines = 0
        for ln in lines:
            if ln.strip().startswith("```"):
                in_fence = not in_fence
                code_lines += 1
                continue
            if in_fence:
                code_lines += 1
            elif _re.match(r"^    \S", ln):
                code_lines += 1
        densities.append((n, code_lines / max(1, len(lines))))
    if densities:
        lo = min(d for _, d in densities)
        hi = max(d for _, d in densities)
        if hi - lo > 0.40:
            sample = [f"ch{n:02d}:{d:.0%}" for n, d in densities[:6]]
            issues.append(
                f"code density varies >40 points across chapters ({', '.join(sample)}...)"
            )

    # 3) Chapter length spread
    lengths = [(n, len(c)) for n, c in chapters]
    if lengths:
        min_len = min(l for _, l in lengths)
        max_len = max(l for _, l in lengths)
        if min_len > 0 and max_len / min_len > 6:
            issues.append(
                f"chapter-length ratio max/min = {max_len // max(1, min_len)}× "
                f"(smallest={min_len}, largest={max_len}) — possibly a stub chapter"
            )
    return issues


def _extract_glossary_terms(
    chapters: list[tuple[int, str]],
    max_terms: int = 12) -> list[str]:
    """
    Pull the most-used CamelCase / snake_case identifiers from the first
    accepted chapter, to serve as a cross-chapter glossary for the curator.
    Purely heuristic, no LLM — gives the curator a list to normalize against.
    """
    if not chapters:
        return []
    first_content = chapters[0][1]
    # Find identifier-looking tokens: CamelCase, snake_case, or dotted.paths
    import re as _re
    tokens = _re.findall(
        r"\b([A-Z][a-zA-Z0-9]+(?:[A-Z][a-zA-Z0-9]+)+|[a-z]+(?:_[a-z0-9]+){1,})\b",
        first_content,
    )
    from collections import Counter
    counts = Counter(tokens)
    return [t for t, _ in counts.most_common(max_terms)]


async def _write_plan_json(
    storage: MinIOStudyStorage,
    study_root: str,
    plan: ChapterPlanList) -> str:
    """
    Persist plan.json at <study_root>/research/plan.json. Returns the object key.
    """
    key = f"{study_root}/research/plan.json"
    await storage.write(
        key,
        plan.model_dump_json(indent = 2),
        content_type = "application/json",
    )
    return key


async def _write_manifest_json(
    storage: MinIOStudyStorage,
    study_root: str,
    manifest: list[ManifestEntry]) -> str:
    """
    Persist the ingestion manifest at <study_root>/research/manifest.json.
    Returns the object key. Called by KnowledgeDistillerGraph.ingest after a tier succeeds.
    """
    key = f"{study_root}/research/manifest.json"
    body = json.dumps(
        [e.model_dump() for e in manifest],
        indent = 2,
        ensure_ascii = False,
    )
    await storage.write(key, body, content_type = "application/json")
    return key


# =============================================================================
# Step 6 — synthesizer + grader + adjustment helpers
# =============================================================================
async def _load_chapter_files(
    storage: MinIOStudyStorage,
    study_root: str,
    slugs: list[str]) -> str:
    """
    Concatenate raw file content for a chapter, labeled by slug, capped at
    CHAPTER_FILES_MAX_CHARS. Returns text formatted for SYNTHESIZER_PROMPT's
    {assigned_files_content} placeholder.
    """
    sections: list[str] = []
    total = 0
    for slug in slugs:
        key = f"{study_root}/research/raw/{slug}.md"
        try:
            body = await storage.read_text(key)
        except Exception as e:
            logger.warning(f"[synth] could not read {key}: {e}")
            continue
        snippet = body.strip()
        sections.append(f"--- {slug}.md ---\n{snippet}\n")
        total += len(snippet)
        if total > CHAPTER_FILES_MAX_CHARS:
            logger.info(
                f"[synth] capping chapter corpus at {total} chars after {len(sections)} files"
            )
            break
    return "\n".join(sections)


def _format_adjustments(adjustments: list[str]) -> str:
    """Format prior adjustments for SYNTHESIZER_PROMPT's {previous_adjustments}."""
    if not adjustments:
        return "(none — first attempt)"
    return "\n\n".join(
        f"ATTEMPT {i+1} ADJUSTMENTS:\n{a}" for i, a in enumerate(adjustments)
    )


def _user_profile_summary(profile: UserProfile) -> str:
    """One-line summary of user profile for GRADER_PROMPT's {user_profile_summary}."""
    return (
        f"level={profile.level}, "
        f"target_markets={profile.target_markets or ['general']}, "
        f"mastered={profile.mastered_technologies[:8] or ['none declared']}, "
        f"portfolio={profile.portfolio_refs[:5] or ['none declared']}"
    )


async def _synthesize_attempt(
    chapter: ChapterPlan,
    files_content: str,
    framework: str,
    tone_block: str,
    previous_adjustments: list[str],
    llm: ChatOpenAI) -> ChapterSynthesis:
    """
    Single synthesis attempt. Pydantic's ChapterSynthesis schema enforces
    shape (content + challenges + 8-15 flashcards).

    Why the None guard:
      `with_structured_output(method="function_calling")` on LangChain's
      fallback chain can return None when the LLM produced no tool_call
      (e.g., the model returned a plain-text apology, or emitted malformed
      arguments that were filtered out). That's NOT raised as an exception
      by LangChain — it just returns None and moves on. A subsequent
      `synthesis.content` access then fails with AttributeError at the
      wrong place (was being caught by the grader's try/except and
      misreported as "Grader failed"). Raising explicitly here triggers
      the fallback chain to try the next model and produces a truthful
      error message if everything ultimately fails.
    """
    chain = SYNTHESIZER_PROMPT | llm.with_structured_output(
        ChapterSynthesis,
        method = "function_calling",
    )
    result = await chain.ainvoke({
        "framework": framework,
        "chapter_number": chapter.number,
        "chapter_title": chapter.title,
        "chapter_goal": chapter.goal,
        "assigned_files_content": files_content,
        "tone_block": tone_block,
        "previous_adjustments": _format_adjustments(previous_adjustments),
    })
    if result is None:
        raise RuntimeError(
            "synthesizer returned None (no tool_call or malformed structured "
            "output) — fallback chain should retry the next model"
        )
    return result


async def _grade_attempt(
    synthesis_text: str,
    chapter: ChapterPlan,
    user_profile: UserProfile,
    framework: str,
    llm: ChatOpenAI) -> GraderEvaluation:
    """
    Run the 8-dimensional adaptive grader on one synthesis attempt. Returns
    structured GraderEvaluation with per-dimension scores, a weighted_score,
    an action ('accept' | 'refine' | 'regenerate'), and a list of specific
    issues to address on the next attempt.

    Same None-guard rationale as _synthesize_attempt — `with_structured_output`
    can return None silently, and a None GraderEvaluation would crash the
    argmax logic immediately after.
    """
    chain = GRADER_PROMPT | llm.with_structured_output(
        GraderEvaluation,
        method = "function_calling",
    )
    result = await chain.ainvoke({
        "framework": framework,
        "user_profile_summary": _user_profile_summary(user_profile),
        "acceptance_threshold": user_profile.acceptance_threshold,
        "assigned_files_list": ", ".join(chapter.assigned_files),
        "synthesis_text": synthesis_text[:GRADER_SYNTHESIS_MAX_CHARS],
    })
    if result is None:
        raise RuntimeError(
            "grader returned None (no tool_call or malformed structured "
            "output) — fallback chain should retry the next model"
        )
    return result


async def _generate_adjustment(
    evaluation: GraderEvaluation,
    synthesis_text: str,
    llm: ChatOpenAI) -> str:
    """
    Turn the grader's evaluation into concrete, actionable synthesizer
    instructions for the next attempt. Plain-text output (no structured
    schema) — interpolated verbatim into SYNTHESIZER_PROMPT's
    {previous_adjustments} slot on the retry.

    Non-critical: if this call fails we continue without a bespoke adjustment
    (grader's specific_issues still surface via the prompt).
    """
    chain = ADJUSTMENT_PROMPT | llm
    try:
        response = await chain.ainvoke({
            "evaluation_json": evaluation.model_dump_json(indent = 2),
            "synthesis_text": synthesis_text[:6_000],
        })
        return response.content.strip()
    except Exception as e:
        logger.warning(f"[synth] adjustment generator failed: {e}; continuing without")
        return "(adjustment generator unavailable; address grader's specific_issues directly)"


async def _write_chapter_artifacts(
    storage: MinIOStudyStorage,
    study_root: str,
    chapter_number: int,
    synthesis: ChapterSynthesis) -> dict:
    """
    Write the three per-chapter artifacts to MinIO under
    `<study_root>/chapter{NN}/`. Returns a partial ChapterResult dict
    (the caller fills in `score` and `iterations`).
    """
    prefix = f"{study_root}/chapter{chapter_number:02d}"
    readme_key = f"{prefix}/README.md"
    await storage.write(readme_key, synthesis.content, content_type = "text/markdown")
    challenges_key = f"{prefix}/challenges.md"
    await storage.write(challenges_key, synthesis.challenges, content_type = "text/markdown")
    flashcards_key = f"{prefix}/flashcards.json"
    flashcards_json = json.dumps(
        [{"front": c.front, "back": c.back} for c in synthesis.flashcards],
        indent = 2,
        ensure_ascii = False,
    )
    await storage.write(flashcards_key, flashcards_json, content_type = "application/json")
    return {
        "number": chapter_number,
        "content_path": readme_key,
        "challenges_path": challenges_key,
        "flashcards_path": flashcards_key,
    }


# =============================================================================
# Step 7 — critic helpers (deterministic citation scan + cross-chapter reads)
# =============================================================================
async def _load_all_chapters(
    storage: MinIOStudyStorage,
    study_root: str,
    plan: list[ChapterPlan]) -> list[tuple[int, str, str]]:
    """
    Read every chapterNN/README.md that exists under study_root.
    Returns [(number, title, body), ...]. Chapters whose README failed to write
    are skipped with a warning — critic still runs on the rest.
    """
    chapters: list[tuple[int, str, str]] = []
    for ch in sorted(plan, key = lambda c: c.number):
        key = f"{study_root}/chapter{ch.number:02d}/README.md"
        try:
            body = await storage.read_text(key)
        except Exception as e:
            logger.warning(f"[critic] chapter {ch.number} README missing at {key}: {e}")
            continue
        chapters.append((ch.number, ch.title, body))
    return chapters


async def _load_available_slugs(
    storage: MinIOStudyStorage,
    study_root: str) -> set[str]:
    """Slugs of every *.md object under <study_root>/research/raw/."""
    keys = await storage.list(f"{study_root}/research/raw/")
    return {
        k.rsplit("/", 1)[-1].removesuffix(".md")
        for k in keys
        if k.endswith(".md")
    }


def _scan_citations(
    chapters: list[tuple[int, str, str]],
    available_slugs: set[str]) -> tuple[set[str], list[str]]:
    """
    Regex-scan every chapter body for '# docs: <slug>' citations. Compare
    against available_slugs.

    Returns:
        (all_cited_slugs, per_chapter_broken_issues)
        where each broken_issue is a string formatted for CriticAssessment.issues
        like 'chapter03: '# docs: quickstart' — source not found in research/raw/'.
    """
    all_cited: set[str] = set()
    issues: list[str] = []
    for number, _title, body in chapters:
        for match in _CITATION_RE.finditer(body):
            raw = match.group(1).strip().rstrip(".,;:)(]}")
            # Strip common extensions
            slug = raw.removesuffix(".md").removesuffix(".txt")
            if not slug:
                continue
            all_cited.add(slug)
            if slug not in available_slugs:
                issues.append(
                    f"chapter{number:02d}: '# docs: {raw}' — source not found in research/raw/"
                )
    return all_cited, issues


def _build_chapter_bundles(chapters: list[tuple[int, str, str]]) -> str:
    """
    Concatenate chapter bodies for the critic prompt's {chapter_bundles}.
    Per-chapter cap + overall cap prevent huge LLM inputs.
    """
    parts: list[str] = []
    total = 0
    for number, title, body in chapters:
        snippet = body[:CRITIC_CHAPTER_MAX_CHARS]
        block = f"=== Chapter {number:02d} — {title} ===\n{snippet}\n"
        parts.append(block)
        total += len(block)
        if total > CRITIC_BUNDLE_MAX_CHARS:
            logger.info(
                f"[critic] bundle cap reached at {total} chars ({len(parts)} chapters)"
            )
            break
    return "\n".join(parts)


# =============================================================================
# Step 8 — assembler helpers (summary.md LLM call + deterministic DEBT.md)
# =============================================================================
async def _load_chapter_previews(
    storage: MinIOStudyStorage,
    study_root: str,
    plan: list[ChapterPlan]) -> list[tuple[int, str, str, str]]:
    """
    Read each chapter's README.md and return a (number, title, goal, preview)
    tuple for the assembler's summary.md generation. Chapters whose README is
    missing get a placeholder preview so the summary can still list them.

    Preview is capped at ASSEMBLER_PREVIEW_CHARS.
    """
    entries: list[tuple[int, str, str, str]] = []
    for ch in sorted(plan, key = lambda c: c.number):
        key = f"{study_root}/chapter{ch.number:02d}/README.md"
        try:
            body = await storage.read_text(key)
            preview = body[:ASSEMBLER_PREVIEW_CHARS].strip()
        except Exception as e:
            logger.warning(f"[assembler] chapter {ch.number} README missing at {key}: {e}")
            preview = "(chapter content unavailable — see DEBT.md)"
        entries.append((ch.number, ch.title, ch.goal, preview))
    return entries


def _build_chapter_summaries(entries: list[tuple[int, str, str, str]]) -> str:
    """
    Format chapter preview data for ASSEMBLER_PROMPT's {chapter_summaries}.
    """
    blocks = []
    for num, title, goal, preview in entries:
        preview_flat = re.sub(r"\s+", " ", preview).strip()
        blocks.append(
            f"### Chapter {num:02d} — {title}\n"
            f"Goal: {goal}\n"
            f"Preview: {preview_flat}"
        )
    return "\n\n".join(blocks)


async def _call_assembler_llm(
    framework: str,
    user_profile_summary_str: str,
    chapter_summaries: str,
    llm: ChatOpenAI) -> str:
    """
    Generate summary.md via ASSEMBLER_PROMPT. Freeform markdown output (no
    structured schema) — the summary is a document, not JSON. Response is
    AIMessage; we return the stripped content string.
    """
    chain = ASSEMBLER_PROMPT | llm
    response = await chain.ainvoke({
        "framework": framework,
        "user_profile_summary": user_profile_summary_str,
        "chapter_summaries": chapter_summaries,
    })
    return response.content.strip()


def _build_debt_md(
    plan: list[ChapterPlan],
    synthesis_results: list[dict],
    validation_report: Optional[dict]) -> str:
    """
    Deterministically assemble DEBT.md from three sources. No LLM.

    1. Grader debts — chapters whose final score fell below the user's
       acceptance_threshold after all Self-Refine iterations were spent.
       Sourced from synthesis_result["debt"] attached in Step 6.
    2. Critic issues — post-synthesis findings from CriticAssessment.issues
       (citation_coverage broken links + LLM-flagged faithfulness issues).
    3. Missing chapters — any plan.chapter whose synthesis_result is absent
       (synthesizer crashed or didn't produce a README).

    If all three sections are empty, writes a single-line "clean" notice.
    """
    lines: list[str] = ["# DEBT — Unresolved Issues", ""]
    dirty = False

    # --- Section 1: grader debts ---------------------------------------------
    grader_debts = [r for r in synthesis_results if r.get("debt")]
    if grader_debts:
        dirty = True
        lines.append("## Chapters Below Grader Threshold")
        lines.append("")
        for r in grader_debts:
            d = r["debt"]
            lines.append(
                f"- **Chapter {r['number']:02d}** — score "
                f"{d['final_score']:.2f} (threshold {d['threshold']:.2f}) "
                f"after {r.get('iterations', '?')} iteration(s)"
            )
            for issue in d.get("specific_issues", [])[:5]:
                # Issue schema (CRITIC, 2026-04-21): span-anchored, dict-shape
                # {span_quote, dimension, suggestion}. Also handle legacy str
                # form for back-compat with older cached debt entries.
                if isinstance(issue, dict):
                    dim = issue.get("dimension", "?")
                    quote = (issue.get("span_quote") or "")[:80]
                    suggestion = issue.get("suggestion", "")
                    lines.append(f"  - **{dim}** — `{quote}` → {suggestion}")
                else:
                    lines.append(f"  - {issue}")
        lines.append("")

    # --- Section 2: critic findings ------------------------------------------
    if validation_report and (validation_report.get("issues") or []):
        dirty = True
        lines.append("## Critic Findings")
        lines.append("")
        lines.append(
            f"Overall score: **{validation_report.get('overall_score', 0):.2f}** — "
            f"citation_coverage={validation_report.get('citation_coverage', 0):.2f}, "
            f"faithfulness={validation_report.get('faithfulness', 0):.2f}, "
            f"code_syntax_valid={validation_report.get('code_syntax_valid', 0):.2f}"
        )
        lines.append("")
        for issue in validation_report.get("issues") or []:
            lines.append(f"- {issue}")
        lines.append("")

    # --- Section 3: missing chapters -----------------------------------------
    planned_numbers = {ch.number for ch in plan}
    synthesized_numbers = {r["number"] for r in synthesis_results}
    missing = planned_numbers - synthesized_numbers
    if missing:
        dirty = True
        lines.append("## Missing Chapters")
        lines.append("")
        by_number = {c.number: c for c in plan}
        for num in sorted(missing):
            ch = by_number.get(num)
            title = ch.title if ch else "?"
            lines.append(
                f"- Chapter {num:02d} — {title}: synthesis did not produce a README.md"
            )
        lines.append("")

    if not dirty:
        lines.append("(No unresolved issues — study is clean.)")
        lines.append("")

    return "\n".join(lines)


def _log_episodic_memory(
    user_id: str,
    framework: str,
    synthesis_results: list[dict],
    validation_report: Optional[dict]) -> None:
    """
    v1 STUB — logs what we'd persist to episodic memory. Full PG table write
    lands in a follow-up step (needs user_episodic_memory schema + auth
    hooks). For now the log line gives us visibility without the DB dep.

    Payload per study run:
      - user_id, framework
      - number of chapters produced
      - average chapter grader score
      - critic overall_score
      - count of chapters that flagged DEBT
    """
    scores = [r["score"] for r in synthesis_results if "score" in r]
    avg_score = f"{sum(scores) / len(scores):.2f}" if scores else "n/a"
    overall = (
        f"{validation_report['overall_score']:.2f}"
        if validation_report and "overall_score" in validation_report
        else "n/a"
    )
    debt_count = sum(1 for r in synthesis_results if r.get("debt"))
    logger.info(
        f"[assembler][episodic] user_id={user_id} framework={framework} "
        f"chapters={len(synthesis_results)} avg_chapter_score={avg_score} "
        f"critic_overall={overall} debt_count={debt_count}"
    )
    # TODO: persist to PG user_episodic_memory table when auth is wired up
