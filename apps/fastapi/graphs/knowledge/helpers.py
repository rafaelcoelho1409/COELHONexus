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
import hashlib
import json
import logging
import os
import re
from typing import Optional

from langchain_openai import ChatOpenAI

from schemas.knowledge.agents import (
    ChapterOutput,
    ChapterPlan,
    ChapterPlanList,
    ChapterSynthesis,
    GraderEvaluation,
    ProseChapterOutput,  # OP-46 (2026-04-25)
)
from schemas.knowledge.ingestion import ManifestEntry
from schemas.knowledge.inputs import UserProfile
from schemas.knowledge.prompts import (
    ADJUSTMENT_PROMPT,
    ASSEMBLER_PROMPT,
    GRADER_PROMPT,
    SYNTHESIZER_PROMPT,
    SYNTHESIZER_PROSE_PROMPT,  # OP-46 (2026-04-25)
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

# Cap on assembled raw-file content fed to the synthesizer in one call —
# TOKEN-based (Tier 1 #2), not char-based. Char-based was unsafe for three
# reasons:
#   1. Off-by-one cap-after-append: a single 10 MB file appended before the
#      budget check produced 1M-token prompts (ch02 2026-04-23 run).
#   2. Providers enforce context windows + TPM in TOKENS; chars are a proxy
#      that systematically under-counts code (~5-6 chars/token) vs prose (~4).
#   3. Tier 0 vault compresses fenced code to ~20-char sentinels BEFORE this
#      content reaches the LLM — so the raw content we pack here can be 25-30%
#      larger in chars than the post-vault prompt we actually send.
# 40_000 tokens post-vault is a comfortable fit inside NIM/Groq context
# windows (128K-262K) and well under rate-limit ceilings for primaries.
# Tiktoken cl100k_base OVER-counts for Qwen/GLM/Kimi/Llama tokenizers, so the
# cap is a conservative upper bound — real token count to the provider is
# typically 10-30% lower.
CHAPTER_FILES_MAX_TOKENS = 40_000

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
# Code-vault primitives (Tier 0a — code preservation)
# =============================================================================
# Extract fenced code blocks from markdown before sending to an LLM, replace
# each with an opaque hash-addressed sentinel, then restore byte-exact after
# the LLM returns. Foundation of the code-preservation invariant documented
# in docs/KNOWLEDGE-DISTILLER-IMPROVEMENTS-ROADMAP.md Tier 0.
#
# Why placeholder substitution vs. "please preserve verbatim" prompting: the
# latter is probabilistic and silently corrupts code (rename identifiers,
# strip comments, elide with ..., reformat whitespace). Placeholders make
# code physically unreachable by the LLM's token generator.
#
# Sentinel shape: <code-ref hash="{sha256[:12]}"/>
#   - Self-closing XML tag: Claude is explicitly trained on XML tags as
#     structural primitives; every mainstream model (GPT, Gemini,
#     open-weights) treats XML tags as pass-through structure, not
#     content to rewrite. Self-closing form signals "no inner content
#     to modify".
#   - 12-char SHA256 prefix of the original fenced block (including fence
#     markers and info string): opaque, hash-addressable, collision space
#     of 2^48 keeps per-document collisions statistically impossible.
#   - Chosen 2026-04-23 AFTER the prior ZWS-wrapped hash shape failed
#     catastrophically on a fastapi smoke test (100% sentinel strip
#     across 4 chapters in the NIM/Groq fallback chain). See
#     KNOWLEDGE-DISTILLER-IMPROVEMENTS-ROADMAP.md Tier 0d for RCA.
# =============================================================================

# Matches the exact sentinel shape this module emits. Used for collision
# detection on input and for unexpected-sentinel detection on LLM output.
_VAULT_SENTINEL_RE = re.compile(r'<code-ref hash="[0-9a-f]{12}"/>')


def _make_sentinel(original: str, salt: int = 0) -> str:
    # salt lets the caller rehash on the astronomically rare per-doc collision
    # (two distinct fences whose sha256[:12] prefixes collide).
    payload = original if salt == 0 else f"{original}|{salt}"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
    return f'<code-ref hash="{digest}"/>'


def _vault_code_blocks(content: str) -> tuple[str, dict[str, str]]:
    """
    Extract every fenced code block from `content`, replace each with an
    opaque sentinel, return (vaulted_text, vault) where vault maps
    sentinel -> original fenced-block text (fence markers + info string +
    body preserved byte-exactly).

    Scope: fenced blocks only (``` and ~~~). Indented code blocks and
    inline `code` spans are NOT vaulted — too common in prose to vault
    safely and lower paraphrasing risk.

    Idempotent on content: same input -> same (vaulted_text, vault).

    Raises ValueError if `content` already contains a vault sentinel
    (indicates a double-vault bug or adversarial input).
    """
    if _VAULT_SENTINEL_RE.search(content):
        raise ValueError(
            "source already contains vault sentinels — cannot safely vault "
            "(possible double-vault or adversarial input)"
        )

    # Local import: markdown-it-py is transitively present via LangChain's
    # text-splitters, but pulling it at module-import time widens the
    # graph-build dep chain unnecessarily.
    from markdown_it import MarkdownIt

    md = MarkdownIt("commonmark")
    tokens = md.parse(content)

    # split("\n") (NOT splitlines()) preserves a trailing empty element when
    # the doc ends with \n, so "\n".join(...) round-trips byte-exactly.
    lines = content.split("\n")

    fence_ranges: list[tuple[int, int, str]] = []
    for tok in tokens:
        if tok.type == "fence" and tok.map is not None:
            start, end = tok.map
            fence_ranges.append((start, end, "\n".join(lines[start:end])))

    if not fence_ranges:
        return content, {}

    fence_ranges.sort(key=lambda r: r[0])

    vault: dict[str, str] = {}
    out_lines: list[str] = []
    i = 0
    fi = 0
    while i < len(lines):
        if fi < len(fence_ranges) and i == fence_ranges[fi][0]:
            _, end, original = fence_ranges[fi]
            sentinel = _make_sentinel(original)
            salt = 0
            while sentinel in vault and vault[sentinel] != original:
                salt += 1
                sentinel = _make_sentinel(original, salt=salt)
            vault[sentinel] = original
            out_lines.append(sentinel)
            i = end
            fi += 1
        else:
            out_lines.append(lines[i])
            i += 1

    return "\n".join(out_lines), vault


def _restore_code_blocks(text: str, vault: dict[str, str]) -> str:
    """
    Reverse `_vault_code_blocks`. Replace every sentinel in `text` with the
    original fenced-block text from `vault`. Sentinels not present in `text`
    are silently skipped — use `_audit_sentinel_roundtrip` to detect drops.
    """
    for sentinel, original in vault.items():
        text = text.replace(sentinel, original)
    return text


def _audit_sentinel_roundtrip(
    llm_output: str,
    vault: dict[str, str]) -> tuple[list[str], list[str]]:
    """
    Report vault integrity on an LLM output BEFORE restoration. Returns
    (missing, unexpected):
      - missing: sentinels present in vault but absent from llm_output
                 (LLM dropped or paraphrased a code block — feed to
                 Self-Refine as targeted correction).
      - unexpected: sentinel-shaped tokens in llm_output not in vault
                    (LLM hallucinated or malformed a sentinel).

    Both lists empty <=> perfect round-trip, preservation_ratio == 1.0.

    OP-21 defensive coerce (2026-04-24): accept non-string input gracefully.
    Some LangChain LLM responses arrive as content-block lists; callers
    should flatten them first, but guard here too.
    """
    if not isinstance(llm_output, str):
        if isinstance(llm_output, list):
            llm_output = "\n".join(str(x) for x in llm_output)
        else:
            llm_output = str(llm_output)
    missing = [s for s in vault if s not in llm_output]
    found = set(_VAULT_SENTINEL_RE.findall(llm_output))
    unexpected = [s for s in found if s not in vault]
    return missing, unexpected


# =============================================================================
# Tier 3 #21 — Structured-output synth audit + assembler
# =============================================================================
# Replaces _audit_sentinel_roundtrip for the structured-output synth path.
# The LLM no longer emits free-form markdown with embedded sentinels — it
# emits ChapterOutput(sections=[Section(heading, prose_md, code_refs)]).
# Audit compares the union of code_refs across sections against the vault's
# bare-hash set; assembler builds final markdown.
_VAULT_HASH_RE = re.compile(r'<code-ref hash="([0-9a-f]{12})"/>')


def _vault_bare_hashes(vault: dict[str, str]) -> set[str]:
    """Extract the 12-hex hash from each sentinel key in the vault."""
    hashes: set[str] = set()
    for sentinel in vault:
        m = _VAULT_HASH_RE.fullmatch(sentinel)
        if m:
            hashes.add(m.group(1))
    return hashes


# OP-23 (2026-04-24 late) — per-section quality thresholds.
# A section with many code_refs but anemic prose is a "code dump" — the LLM
# listed hashes without explaining anything. Run-10 ch03 had sections with
# 4-5 consecutive code blocks and one filler sentence between. Force refine
# when (prose_words / max(1, len(code_refs))) < _MIN_WORDS_PER_CODEREF.
# 40 words ≈ 2-3 complete sentences per code block, which is the minimum
# "teach, don't dump" bar.
_MIN_WORDS_PER_CODEREF = 40
# If the whole chapter has zero `# docs:` citation markers across ALL prose
# AND there are non-trivial sections, that's an unbacked-claims failure
# (Run-10 ch06 had 26 H2s and zero citations). Synthesized as a special
# thin_sections entry "__zero_citations__" so format_feedback handles it.
_CITATION_MARKER_RE = re.compile(r"^\s*#\s*docs:\s*\S+", re.MULTILINE)
_ZERO_CITATIONS_MARKER = "__zero_citations__"


def _audit_structured_output_refs(
    output,  # ChapterOutput (typed loosely to avoid a schema import cycle)
    vault: dict[str, str],
) -> tuple[list[str], list[str], list[str], list[str], list[str], list[str]]:
    """
    Audit a ChapterOutput against the vault. Returns 6 lists:
    (missing, invented, fence_sections, duplicated_refs, empty_sections,
     thin_sections):
      - missing: bare-hash values in the vault that no Section.code_refs
                 mentions (LLM didn't reference a code block — unusable
                 output, force refine with targeted preview feedback).
      - invented: bare-hash values that appear in code_refs but NOT in the
                  vault (LLM fabricated a hash).
      - fence_sections: headings of Section entries whose prose_md contains
                       a ``` fence (schema rule violation).
      - duplicated_refs: bare-hash values that appear in MORE THAN ONE
                        section's code_refs — the LLM's audit-loophole
                        exploitation observed on Run-4 (batch-3 fix).
                        Every vault hash must appear in exactly ONE section.
      - empty_sections: headings of Section entries with zero code_refs
                       AND non-trivial prose. Pure-transition sections
                       (≤40 chars of prose) are allowed to be empty; flag
                       substantive prose-only sections as distribution
                       failures so the LLM redistributes on refine.
      - thin_sections: OP-23 (2026-04-24). Headings of Section entries
                      where prose_words / max(1, len(code_refs)) < 40
                      (code dumps with insufficient explanation). Also
                      contains the sentinel "__zero_citations__" if the
                      entire chapter has zero `# docs:` markers despite
                      having ≥3 sections with non-trivial prose.

    All six lists empty <=> perfect output, ready for assembly.
    """
    vault_hashes = _vault_bare_hashes(vault)
    referenced: set[str] = set()
    ref_counts: dict[str, int] = {}
    fence_sections: list[str] = []
    empty_sections: list[str] = []
    thin_sections: list[str] = []
    nontrivial_section_count = 0
    total_citation_markers = 0
    for section in output.sections:
        for ref in section.code_refs:
            referenced.add(ref)
            ref_counts[ref] = ref_counts.get(ref, 0) + 1
        # Fence check — schema discourages but doesn't reject at Pydantic
        # level (see schema module docstring for rationale).
        if "```" in section.prose_md:
            fence_sections.append(section.heading or "<unnamed>")
        # Distribution check — non-trivial section with no code is a
        # prose-stuffing tell.
        #
        # OP-9 (2026-04-24, post-Run-9): raised threshold 40 → 120 chars.
        # Run-9 ch07 sentinel'd at iter 4 with audit = (0, 0, 0, 0, 2 empty)
        # — the two "empty" sections were legit 60-90 char transition
        # paragraphs like "Let's now tie these concepts together before
        # examining the runtime." That's valid chapter narrative, not a
        # prose-stuffing failure. 120 chars ≈ 20-25 words ≈ a full
        # concrete sentence; below that, prose-only is fine.
        _EMPTY_SECTION_MIN_PROSE = 120
        prose_stripped = section.prose_md.strip()
        if (not section.code_refs
                and len(prose_stripped) > _EMPTY_SECTION_MIN_PROSE
                and vault_hashes):
            empty_sections.append(section.heading or "<unnamed>")
        # OP-23a — thin-section check. Only applies when the section HAS
        # code_refs (code dumps with no teaching). Words are space-split
        # tokens in prose_md minus the citation marker lines which don't
        # count as teaching prose.
        if section.code_refs:
            prose_no_citations = _CITATION_MARKER_RE.sub("", prose_stripped)
            word_count = len(prose_no_citations.split())
            ratio = word_count / max(1, len(section.code_refs))
            if ratio < _MIN_WORDS_PER_CODEREF:
                thin_sections.append(
                    f"{section.heading or '<unnamed>'} "
                    f"({word_count}w/{len(section.code_refs)}refs="
                    f"{ratio:.0f}w/ref)"
                )
        # Track chapter-level stats for zero-citation gate.
        if len(prose_stripped) > _EMPTY_SECTION_MIN_PROSE:
            nontrivial_section_count += 1
        total_citation_markers += len(_CITATION_MARKER_RE.findall(section.prose_md))
    # OP-23b — chapter-level zero-citation gate. Any chapter with ≥3
    # non-trivial sections but zero `# docs:` citations is unbacked
    # claims — force refine. Run-10 ch06 was the canonical failure case.
    if nontrivial_section_count >= 3 and total_citation_markers == 0:
        thin_sections.append(_ZERO_CITATIONS_MARKER)
    # OP-73 (2026-04-25, post-Run-13) — stricter citation-density gate.
    # Run-13 ch06 had ZERO `# docs:` citations across 1543 lines and slipped
    # past the OP-23b gate (likely because nontrivial_section_count was off
    # for that chapter). Tighten: require minimum citation density of
    # ≥3 citations per 1000 prose words. Falls back to OP-23b's binary
    # zero-check for very short chapters where density math is noisy.
    _total_prose_words = sum(
        len(_CITATION_MARKER_RE.sub("", s.prose_md.strip()).split())
        for s in output.sections
    )
    if _total_prose_words >= 500:
        _citations_per_1000 = (total_citation_markers * 1000) / _total_prose_words
        if _citations_per_1000 < 3.0 and _ZERO_CITATIONS_MARKER not in thin_sections:
            thin_sections.append(_ZERO_CITATIONS_MARKER)
    # OP-57 (2026-04-25, post-Run-13) — chapter-intro enforcement.
    # OP-38 added a synth-prompt instruction for first section to open with
    # a 2-3 sentence orientation paragraph BEFORE any code. Run-13 evidence:
    # only ch06 followed it; ch01/03/05 dive straight into prose-then-code.
    # Add an audit check: first section's prose_md must have ≥2 complete
    # sentences (≥40 chars each, ending in `.!?`) before any inline `code`
    # span or `# docs:` citation marker. Sentinel marker: `__no_chapter_intro__`.
    if output.sections:
        _first_prose = output.sections[0].prose_md.strip()
        # Grab the prefix BEFORE any inline backtick or citation marker.
        _prefix_match = re.match(
            r"((?:[^`\n]|\n(?!\s*#\s*docs:))*?)(?=`|\n\s*#\s*docs:|$)",
            _first_prose, re.DOTALL,
        )
        _prefix = _prefix_match.group(1).strip() if _prefix_match else ""
        # Count complete sentences in the prefix.
        _sentences = [
            s.strip() for s in re.split(r"(?<=[.!?])\s+", _prefix)
            if len(s.strip()) >= 40 and s.strip().endswith((".", "!", "?"))
        ]
        if len(_sentences) < 2:
            thin_sections.append("__no_chapter_intro__")
    missing = sorted(vault_hashes - referenced)
    invented = sorted(referenced - vault_hashes)
    duplicated_refs = sorted(h for h, c in ref_counts.items() if c > 1)
    return (missing, invented, fence_sections, duplicated_refs,
            empty_sections, thin_sections)


def _format_structured_output_feedback(
    missing: list[str],
    invented: list[str],
    fence_sections: list[str],
    vault: dict[str, str],
    duplicated_refs: list[str] | None = None,
    empty_sections: list[str] | None = None,
    thin_sections: list[str] | None = None,
) -> str:
    """
    Build targeted adjustment text for the Self-Refine loop when a
    ChapterOutput audit fails. Consumed via `_format_adjustments`.
    Mirrors `_format_preservation_feedback` but speaks the schema's
    language: bare hashes + section headings.
    """
    # bare_hash -> sentinel -> vault[sentinel] (original fenced-block text)
    hash_to_sentinel = {
        m.group(1): sentinel
        for sentinel in vault
        for m in [_VAULT_HASH_RE.fullmatch(sentinel)] if m
    }
    parts = [
        "**STRUCTURED OUTPUT FAILURE (hard requirement — forces a retry "
        "even if other grader dimensions scored well).**"
    ]
    if missing:
        parts.append(
            f"\nYour sections' `code_refs` did not reference "
            f"{len(missing)} of {len(hash_to_sentinel)} vault hashes. "
            "Every `<code-ref hash=\"<12-hex>\"/>` in the input MUST have "
            "its 12-hex hash appear in at least one section's `code_refs` "
            "list — the system substitutes the real code block back AFTER "
            "your response using that hash. Missing hashes (preview shows "
            "the original block so you can place it in the right section):"
        )
        for h in missing[:8]:
            original = vault.get(hash_to_sentinel.get(h, ""), "")
            preview = original.replace("\n", " ⏎ ")[:120]
            parts.append(f"  - `{h}` → was: {preview}")
        if len(missing) > 8:
            parts.append(f"  - (+{len(missing) - 8} more not shown)")
        parts.append(
            "Add each missing hash to the `code_refs` list of the section "
            "where the code logically belongs, in the order the reader "
            "should encounter it."
        )
    if invented:
        parts.append(
            f"\nYour output invented {len(invented)} hash value(s) not in "
            "the input: "
            f"{', '.join(repr(h) for h in invented[:5])}"
            f"{' ...' if len(invented) > 5 else ''}. "
            "Only use 12-hex hashes that appeared inside "
            "`<code-ref hash=\"...\"/>` tags in the input. Do not invent "
            "new ones — invented hashes can't be resolved and fail the chapter."
        )
    if fence_sections:
        parts.append(
            f"\nThese section(s) contained triple-backtick ``` fences in "
            f"`prose_md`, which is forbidden — use `code_refs` instead: "
            f"{', '.join(repr(h) for h in fence_sections[:5])}"
            f"{' ...' if len(fence_sections) > 5 else ''}."
        )
    if duplicated_refs:
        parts.append(
            f"\nYour output placed {len(duplicated_refs)} hash(es) in MORE "
            f"THAN ONE section's `code_refs`. Every vault hash must appear "
            "in EXACTLY ONE section — the one where the code is topically "
            "most relevant. Duplicated hashes cause the same code block to "
            "be rendered repeatedly in unrelated sections (observed "
            f"distribution failure). Hashes appearing >1x: "
            f"{', '.join(repr(h) for h in duplicated_refs[:5])}"
            f"{' ...' if len(duplicated_refs) > 5 else ''}. Pick the single "
            "best section for each and remove from the others."
        )
    if empty_sections:
        parts.append(
            f"\nThese section(s) have substantive prose but zero `code_refs`: "
            f"{', '.join(repr(h) for h in empty_sections[:5])}"
            f"{' ...' if len(empty_sections) > 5 else ''}. Either (a) add "
            "the relevant vault hash(es) to each section's code_refs, or "
            "(b) merge the section's prose into a neighboring section that "
            "has code, or (c) shorten the prose to a ≤40-char transition. "
            "Prose-only sections in a code-framework study are a "
            "distribution failure."
        )
    if thin_sections:
        # OP-23 (2026-04-24). Three feedback paths share this list:
        # (a) code-dump sections with <40 words/code_ref,
        # (b) chapter-level zero-citation sentinel "__zero_citations__",
        # (c) OP-57 missing-chapter-intro sentinel "__no_chapter_intro__".
        real_thin = [h for h in thin_sections
                     if h not in (_ZERO_CITATIONS_MARKER, "__no_chapter_intro__")]
        has_zero_cit = _ZERO_CITATIONS_MARKER in thin_sections
        has_no_intro = "__no_chapter_intro__" in thin_sections
        if real_thin:
            parts.append(
                f"\nThese section(s) are **code dumps** — they list multiple "
                f"code_refs but have insufficient explanatory prose "
                f"(<{_MIN_WORDS_PER_CODEREF} words per code block): "
                f"{', '.join(repr(h) for h in real_thin[:5])}"
                f"{' ...' if len(real_thin) > 5 else ''}. "
                "For each listed section, add 2-3 sentences of concrete "
                "explanation BEFORE each code block: what the snippet does, "
                "when to use it, what the non-obvious parameter/return is. "
                "Do NOT pad with generic filler — each sentence must add "
                "information the reader cannot see from the code alone."
            )
        if has_zero_cit:
            parts.append(
                "\nThis chapter has **zero `# docs:` citation markers** "
                "across all sections, despite having substantive prose. "
                "Every non-trivial claim must cite a source file from the "
                "input corpus. Add `# docs: <file_slug>` lines to the "
                "prose_md of each section — pick the file slug(s) that "
                "the prose's factual claims actually came from. Bare "
                "citation lines are preserved by the assembler; without "
                "them the chapter is unbacked opinion."
            )
        if has_no_intro:
            parts.append(
                "\nThe **first section is missing its orientation paragraph** "
                "(OP-38). It must open with 2-3 complete sentences (each ≥40 "
                "chars, ending in `.` `!` `?`) BEFORE any `code` span or "
                "`# docs:` citation marker. Cover: what the reader will "
                "learn in this chapter, why it matters in production, what "
                "prerequisites are assumed. THEN dive into prose-then-code "
                "sections. Without this orientation, cold readers can't "
                "enter the chapter."
            )
    return "\n".join(parts)


# =============================================================================
# OP-22 + OP-28 + OP-29 (2026-04-24 late) — assembled-markdown hygiene scrubber
# =============================================================================
# Runs once per chapter AFTER _assemble_chapter_markdown. Three passes:
#
#   OP-22 — raw-corpus + Mintlify-tag leakage (Run-10: ch02, ch03×3, ch08×2).
#           The synth LLM occasionally bleeds unsynthesized corpus fragments
#           into prose_md: bare `--- docs-foo.md ---` file-boundary markers
#           from the MAP-shard input format, or orphan Mintlify tags like
#           `<Tabs>` / `<CodeGroup>` that don't render outside Mintlify.
#
#   OP-28 — fence-count integrity (Run-10: ch08 had 33 close vs 29 open).
#           Imbalanced ``` fences break markdown rendering in every viewer.
#           We trim trailing content past the last unmatched open fence, or
#           append a closing fence if the trailing region is short.
#
#   OP-29 — inline backtick sanity. The fence-contamination audit in
#           _audit_structured_output_refs checks for `^```` line-start
#           fences only. Stray ``` embedded in a markdown table cell or
#           blockquote body (e.g. `> some prose ``` inline`) slips through.
#           Rare but nonzero; normalize to inline-code span.
#
# Defensive: on any exception the input is returned unchanged (a partially
# broken chapter is better than no chapter at all).

_RAW_CORPUS_BOUNDARY_RE = re.compile(
    r"^\s*-{3,}\s*docs[\-_][^\s]+\.(?:md|txt|rst)\s*-{3,}\s*$",
    re.MULTILINE,
)
# Mintlify-specific structural tags (used by Stripe/Mintlify/docs platforms).
# These only render inside Mintlify — anywhere else they're noise that signals
# the LLM re-emitted raw corpus instead of rewriting.
_MINTLIFY_TAGS = (
    "Tabs", "Tab", "Accordion", "AccordionGroup",
    "Warning", "Tip", "Note", "Info", "Caution",
    "CodeGroup", "Expandable", "ParamField", "ResponseField",
    "Card", "CardGroup", "Frame", "Check",
)
_MINTLIFY_ORPHAN_RE = re.compile(
    r"<(?:" + "|".join(_MINTLIFY_TAGS) + r")(?:\s[^>]*)?>"
    r"|</(?:" + "|".join(_MINTLIFY_TAGS) + r")>",
    re.IGNORECASE,
)

# OP-36 (2026-04-25) — Mintlify code-fence metadata scrubber.
# Run-11 evidence: every code block in LangChain output had
# `theme={"theme":{"light":"catppuccin-latte","dark":"catppuccin-mocha"}}`
# trailing the language tag on the fence opener line. Mintlify renders this
# (it's their syntax-highlight theme switcher), but anywhere else it's
# noise on every single code block. Also catches: `expandable`, `lines`,
# `title="..."`, `wrap`, `icon="..."`, `actions={...}`, and other Mintlify
# fence attributes. Strips everything from the language tag onward.
_MINTLIFY_FENCE_META_RE = re.compile(
    # Anchors at line start, captures: leading whitespace + ``` + optional
    # language tag, then captures everything else on the line up to EOL.
    # Replacement keeps the prefix (whitespace + ``` + lang) and drops the rest.
    r"^(\s*```[a-zA-Z0-9_+\-]*)\s+(?:theme=|expandable\b|lines\b|title=|wrap\b|icon=|actions=|highlight=|focus=|filename=|copy=)[^\n]*$",
    re.MULTILINE,
)


def _scrub_assembled_markdown(md: str) -> tuple[str, dict[str, int]]:
    """
    OP-22/28/29 post-assembly hygiene. Returns (cleaned_md, stats) where
    stats counts what was removed/fixed per pass. Non-raising — on
    unexpected failure the original md comes back with empty stats.
    """
    stats = {
        "raw_corpus_boundaries": 0,
        "mintlify_orphans": 0,
        "mintlify_fence_meta": 0,
        "fence_balance_fixed": 0,
        "inline_fence_sanitized": 0,
        "stacked_citations_split": 0,
        "literal_newlines_fixed": 0,        # OP-55
        "citation_extensions_stripped": 0,  # OP-69
        "truncated_blocks_repaired": 0,     # OP-70
    }
    try:
        # Pass 0 (OP-36) — Mintlify fence metadata stripper. Runs FIRST so
        # subsequent passes see clean fence opener lines. Replaces lines like
        # ` ```python theme={"theme":{...}} `  →  ` ```python `. Same for
        # `expandable`, `lines`, `title=`, `wrap`, `icon=`, `actions=`, etc.
        meta_matches = _MINTLIFY_FENCE_META_RE.findall(md)
        if meta_matches:
            md = _MINTLIFY_FENCE_META_RE.sub(r"\1", md)
            stats["mintlify_fence_meta"] = len(meta_matches)

        # Pass 1 (OP-22a) — raw `--- docs-foo.md ---` boundary markers.
        boundaries = _RAW_CORPUS_BOUNDARY_RE.findall(md)
        if boundaries:
            md = _RAW_CORPUS_BOUNDARY_RE.sub("", md)
            stats["raw_corpus_boundaries"] = len(boundaries)

        # Pass 2 (OP-22b) — orphan Mintlify tags. A tag is "orphan" if it
        # appears OUTSIDE a fenced code block (inside code is legit —
        # doc snippets). We scan line-by-line and only strip tags in
        # prose regions. We don't try to pair open/close tags — just
        # drop every occurrence; since we're reassembling prose, the
        # LLM's intent is the prose around the tag, not the tag itself.
        out_lines: list[str] = []
        in_fence = False
        mintlify_hits = 0
        for line in md.splitlines():
            stripped = line.strip()
            # Fence-toggle line starts with ``` (allow language tag after).
            if stripped.startswith("```"):
                in_fence = not in_fence
                out_lines.append(line)
                continue
            if in_fence:
                out_lines.append(line)
                continue
            # Prose region — strip orphan Mintlify tags. We collapse
            # lines that become empty after stripping to preserve
            # paragraph spacing.
            new_line, n = _MINTLIFY_ORPHAN_RE.subn("", line)
            if n:
                mintlify_hits += n
                if new_line.strip() == "":
                    # entire line was a tag-only line — drop it.
                    continue
            out_lines.append(new_line)
        md = "\n".join(out_lines)
        stats["mintlify_orphans"] = mintlify_hits

        # Pass 3 (OP-28) — fence-count integrity. Count top-of-line ``` fences.
        # If odd, we have an unclosed fence → append one. Excess closings we
        # leave alone (they'll rarely render wrong on their own).
        fence_lines = [ln for ln in md.splitlines() if ln.lstrip().startswith("```")]
        if len(fence_lines) % 2 == 1:
            md = md.rstrip() + "\n```\n"
            stats["fence_balance_fixed"] = 1

        # Pass 4 (OP-29) — inline ``` that somehow survived line-start audit.
        # Only sanitize OUTSIDE fenced blocks; inside a block they're legit.
        # We scan again with the in_fence state to replace stray non-line-
        # start ``` occurrences with a visible marker (single backtick span).
        out2: list[str] = []
        in_fence = False
        inline_hits = 0
        for line in md.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("```"):
                in_fence = not in_fence
                out2.append(line)
                continue
            if in_fence:
                out2.append(line)
                continue
            # In prose. Any remaining ``` here is stray.
            if "```" in line:
                line = line.replace("```", "`")
                inline_hits += 1
            out2.append(line)
        md = "\n".join(out2)
        stats["inline_fence_sanitized"] = inline_hits

        # Pass 5 (OP-37, 2026-04-25) — stacked citation reformatter.
        # Run-11 evidence: prose lines often had `# docs: foo.md # docs: bar.md
        # # docs: baz.md` — 4-6 citations concatenated on one line, hurting
        # readability. Split each `# docs: ...` onto its own line. Skip lines
        # inside fenced code blocks (citations inside code are intentional).
        out3: list[str] = []
        in_fence = False
        stacked_hits = 0
        _stacked_re = re.compile(r"\s+(?=#\s*docs:\s*\S)")
        for line in md.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("```"):
                in_fence = not in_fence
                out3.append(line)
                continue
            if in_fence:
                out3.append(line)
                continue
            # Prose region. Count `# docs:` markers; if >1, split.
            n_markers = len(re.findall(r"#\s*docs:\s*\S", line))
            if n_markers > 1:
                # Use single-marker split: insert newlines BEFORE each
                # `# docs:` marker except the first occurrence on the line.
                first_idx = line.find("#")
                # Apply the split to the part AFTER the first marker.
                if first_idx >= 0:
                    head = line[:first_idx]
                    tail = line[first_idx:]
                    pieces = _stacked_re.split(tail)
                    if len(pieces) > 1:
                        out3.append((head + pieces[0]).rstrip())
                        for p in pieces[1:]:
                            out3.append(p.rstrip())
                        stacked_hits += len(pieces) - 1
                        continue
            out3.append(line)
        md = "\n".join(out3)
        stats["stacked_citations_split"] = stacked_hits

        # Pass 6 (OP-55, 2026-04-25 post-Run-13) — literal `\n\n` → real newlines.
        # Run-13 evidence: synth LLM occasionally emits JSON escape sequences
        # in prose_md strings (`# docs: foo.md\n\nNext paragraph...`) that get
        # preserved literally during assembly and render as 4 visible chars
        # instead of paragraph breaks. Convert in PROSE only (not inside
        # fenced code where they may be intentional like in `printf "\n"`).
        out4: list[str] = []
        in_fence = False
        literal_nl_hits = 0
        for line in md.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("```"):
                in_fence = not in_fence
                out4.append(line)
                continue
            if in_fence:
                out4.append(line)
                continue
            if "\\n" in line:
                # Replace literal \n\n → blank line, single \n → linebreak.
                # Splitlines handles the actual newline insertion.
                new_line = line.replace("\\n\\n", "\n\n").replace("\\n", "\n")
                if new_line != line:
                    literal_nl_hits += new_line.count("\n") - line.count("\n")
                    out4.extend(new_line.split("\n"))
                    continue
            out4.append(line)
        md = "\n".join(out4)
        stats["literal_newlines_fixed"] = literal_nl_hits

        # Pass 7 (OP-69, 2026-04-25 post-Run-13) — citation slug normalizer.
        # Run-13 ch01 had `# docs: docs-docker-com-engine-manage-resources-contexts.md`
        # — the slug should be without `.md`/`.txt`/`.rst` extension to match
        # the resolver-emitted slug format used in research/raw/. Strip
        # extensions from any citation marker line.
        _citation_ext_re = re.compile(
            r"(#\s*docs:\s*\S+?)\.(?:md|txt|rst)\b"
        )
        ext_hits = len(_citation_ext_re.findall(md))
        if ext_hits:
            md = _citation_ext_re.sub(r"\1", md)
            stats["citation_extensions_stripped"] = ext_hits

        # Pass 8 (OP-70, 2026-04-25 post-Run-13) — truncated code block detector.
        # Run-13 ch01 had `--icc Enab` at end of code block (truncated mid-word
        # before closing fence); ch07 had `RUN ap` similar. Detect: any code
        # block whose LAST content line ends in a partial token (no whitespace
        # at end + line under 60 chars) AND no period/quote/semicolon at end.
        # Repair by appending `…` placeholder to make the truncation visible.
        out5: list[str] = []
        in_fence = False
        fence_buffer: list[str] = []
        truncated_hits = 0
        for line in md.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("```"):
                if in_fence and fence_buffer:
                    # Closing fence — inspect last content line for truncation
                    last = fence_buffer[-1]
                    last_stripped = last.rstrip()
                    # OK terminators: balanced punctuation + numerics + common
                    # closer chars suggesting "this is a complete statement".
                    _OK_END_CHARS = set("}.\";)$|>0123456789]'`/-+=:!?")
                    looks_truncated = (
                        last_stripped
                        and len(last_stripped) < 60
                        and last_stripped[-1] not in _OK_END_CHARS
                    )
                    if looks_truncated:
                        fence_buffer[-1] = last_stripped + " # ...(truncated)"
                        truncated_hits += 1
                    out5.extend(fence_buffer)
                    fence_buffer = []
                in_fence = not in_fence
                out5.append(line)
                continue
            if in_fence:
                fence_buffer.append(line)
            else:
                out5.append(line)
        if fence_buffer:
            # Unclosed fence at EOF — flush as-is (OP-28 will close it)
            out5.extend(fence_buffer)
        md = "\n".join(out5)
        stats["truncated_blocks_repaired"] = truncated_hits

        return md, stats
    except Exception as e:  # pragma: no cover — defensive
        logger.warning(f"[scrub] _scrub_assembled_markdown failed: {e}")
        return md, stats


def _assemble_chapter_markdown(
    output,  # ChapterOutput
    vault: dict[str, str],
    chapter_title: str | None = None,
) -> str:
    """
    Build the final chapter markdown from a ChapterOutput + the vault:
      # <chapter_title>      (only if provided — curator preserves)
      ## <section.heading>
      <section.prose_md>
      <fence for code_refs[0]>
      <fence for code_refs[1]>
      ...
    Code fences are resolved deterministically from the vault; a code_ref
    whose bare hash isn't in the vault is skipped (audit would've caught
    this — if we're assembling, it means the audit passed). Blank lines
    separate each block.

    OP-22/28/29 (2026-04-24 late): after assembly, runs `_scrub_assembled_markdown`
    to strip raw-corpus leakage, orphan Mintlify tags, rebalance unmatched
    fences, and sanitize stray inline ``` in prose. Stats are logged at INFO
    level so Run-N post-mortems can see whether the LLM is improving or if
    scrubbing is doing heavy lifting.
    """
    hash_to_sentinel = {
        m.group(1): sentinel
        for sentinel in vault
        for m in [_VAULT_HASH_RE.fullmatch(sentinel)] if m
    }
    parts: list[str] = []
    # Batch-3 defense (2026-04-23): even if the LLM put the same hash in
    # multiple sections' code_refs (audit-loophole observed Run-4), each
    # vault block is emitted at MOST once. First occurrence wins — the
    # placement is usually the LLM's first/best choice. Audit flags
    # duplicated_refs on the same iteration so refine catches the root
    # cause; this dedup prevents the visible duplicate even if audit is
    # bypassed by a future schema change.
    emitted_refs: set[str] = set()
    if chapter_title:
        parts.append(f"# {chapter_title}\n")
    for section in output.sections:
        heading = (section.heading or "").strip()
        if heading:
            parts.append(f"## {heading}\n")
        prose = section.prose_md.strip()
        if prose:
            parts.append(prose + "\n")
        for ref in section.code_refs:
            if ref in emitted_refs:
                continue  # defensive dedup — see comment above
            sentinel = hash_to_sentinel.get(ref)
            if not sentinel:
                continue  # audit already flagged invented refs
            parts.append(vault[sentinel] + "\n")
            emitted_refs.add(ref)
    assembled = "\n".join(parts).rstrip() + "\n"
    cleaned, scrub_stats = _scrub_assembled_markdown(assembled)
    if any(scrub_stats.values()):
        logger.info(
            "[assembler] scrub applied: "
            f"raw_corpus_boundaries={scrub_stats['raw_corpus_boundaries']} "
            f"mintlify_orphans={scrub_stats['mintlify_orphans']} "
            f"mintlify_fence_meta={scrub_stats['mintlify_fence_meta']} "
            f"fence_balance_fixed={scrub_stats['fence_balance_fixed']} "
            f"inline_fence_sanitized={scrub_stats['inline_fence_sanitized']} "
            f"stacked_citations_split={scrub_stats['stacked_citations_split']} "
            f"literal_newlines_fixed={scrub_stats['literal_newlines_fixed']} "
            f"citation_extensions_stripped={scrub_stats['citation_extensions_stripped']} "
            f"truncated_blocks_repaired={scrub_stats['truncated_blocks_repaired']}"
        )
    return cleaned


def _format_preservation_feedback(
    missing: list[str],
    unexpected: list[str],
    vault: dict[str, str]) -> str:
    """
    Build a targeted adjustment string for the Self-Refine loop when the
    synthesizer or curator fails to preserve code-block sentinels.
    Feeds into the `adjustments` list consumed by `_format_adjustments`.

    Each missing sentinel includes a short preview of its original fenced
    block so the LLM can re-insert the sentinel near its correct position.
    Cap at 8 previews to keep the retry prompt small.
    """
    parts = [
        "**PRESERVATION FAILURE (hard requirement — forces a retry even "
        "if other grader dimensions scored well).**"
    ]
    if missing:
        parts.append(
            f"\nYour previous output dropped {len(missing)} of "
            f"{len(vault)} code-block sentinels. You MUST reproduce every "
            "`<code-ref hash=\"...\"/>` tag from the input byte-for-byte "
            "in your `content` output. The following sentinels were "
            "missing (preview shows the original block they stand in for):"
        )
        for sentinel in missing[:8]:
            original = vault[sentinel]
            preview = original.replace("\n", " ⏎ ")[:120]
            parts.append(f"  - `{sentinel}` → was: {preview}")
        if len(missing) > 8:
            parts.append(f"  - (+{len(missing) - 8} more not shown)")
        parts.append(
            "Copy each sentinel byte-for-byte where the code should "
            "appear. Do not paraphrase, summarize, or replace them with "
            "actual code — the system substitutes real code back after "
            "your response."
        )
    if unexpected:
        parts.append(
            f"\nYour previous output contained {len(unexpected)} "
            "sentinel-shaped tokens that were NOT in the input — you "
            "invented them. Sentinels can only be COPIED from the input; "
            "never fabricate sentinel hashes. Invented sentinels cannot "
            "be restored and fail the chapter. Invented tokens (sample): "
            f"{', '.join(repr(s) for s in unexpected[:3])}."
        )
    return "\n".join(parts)


# =============================================================================
# Step 5 — planner helpers
# =============================================================================
# =============================================================================
# Pre-MAP noise filter — semantic off-topic (2026-05-09)
# =============================================================================
# Replaces the legacy regex-based `_filter_noise_files` (Tier 4 #17, 2026-04-24).
# The old approach maintained a hand-curated list of slug patterns
# (changelog/license/contributing/...). It missed framework-specific noise
# without matching slugs (Terragrunt's 160-file `/contribute*` + `/developing-*`
# junk-drawer Chapter 3 was the canonical failure case).
#
# Replacement: embed each doc + a "teaching-content prototype" anchored on
# the framework name, drop any doc whose cosine similarity to the prototype
# is below `_OFF_TOPIC_THRESHOLD`. Single Xinference batch call (~1s for 400
# docs); fully deterministic; zero per-framework calibration.
#
# Why this beats regex:
#   - Catches every framework's contributing/release-process/community docs
#     without a pre-curated pattern list.
#   - Boilerplate (license, cookies) still gets dropped — those texts have
#     low cosine to a "how to use" prototype regardless of slug shape.
#   - Stub / pure-nav pages also score low (no teaching content → low cos).
# =============================================================================
_OFF_TOPIC_THRESHOLD = 0.30  # cosine; tunable per framework if a corpus repeatedly mis-classifies
_OFF_TOPIC_PROTOTYPE_TEMPLATE = (
    "Documentation explaining how to use {framework}: "
    "features, configuration, usage examples, and tutorials."
)
_OFF_TOPIC_SNIPPET_CHARS = 1500  # bytes of body fed into the embedder per doc.
# Bumped 200 → 1500 on 2026-05-09 night for parity with classical_map.py's
# PREVIEW_CHARS. Matches NV-Embed's optimal input length (256-512 tokens);
# sharper cosine separation between teaching content and contributing/release-
# process docs vs the prior 200-char "intro paragraph only" cap.


async def _filter_off_topic_files(
    entries: list[tuple[str, str]],
    framework: str,
    threshold: float = _OFF_TOPIC_THRESHOLD,
) -> list[tuple[str, str]]:
    """
    Drop docs that don't semantically resemble teaching content for
    `framework`, using cosine similarity to a prototype anchor.

    Args:
        entries:   list of (slug, body) tuples from research/raw/.
        framework: framework slug (e.g., "terragrunt"). Used in the prototype
                   text so the threshold separates teaching content from
                   contributing/release-process/community/legal docs.
        threshold: cosine similarity floor; docs below get dropped.

    Returns:
        entries minus off-topic docs, preserving input order. On any
        embedding failure, returns input unchanged (defensive: a broken
        embedding service must not block the planner).
    """
    if not entries:
        return entries
    try:
        import numpy as np
        from services.knowledge.embeddings import embed_texts
        prototype_text = _OFF_TOPIC_PROTOTYPE_TEMPLATE.format(framework=framework)
        snippets = [
            (body or "").strip()[:_OFF_TOPIC_SNIPPET_CHARS] or " "
            for _, body in entries
        ]
        all_texts = [prototype_text] + snippets
        vectors_list, provider = await embed_texts(all_texts)
        vectors = np.asarray(vectors_list, dtype=np.float32)
        proto = vectors[0]
        docs = vectors[1:]
        proto_norm = float(np.linalg.norm(proto))
        doc_norms = np.linalg.norm(docs, axis=1)
        cosines = (docs @ proto) / np.maximum(doc_norms * proto_norm, 1e-12)
        kept: list[tuple[str, str]] = []
        dropped_samples: list[str] = []
        for (slug, body), cos in zip(entries, cosines):
            if float(cos) >= threshold:
                kept.append((slug, body))
            elif len(dropped_samples) < 5:
                dropped_samples.append(slug)
        dropped = len(entries) - len(kept)
        if dropped > 0:
            logger.info(
                f"[off-topic-filter] dropped {dropped}/{len(entries)} docs "
                f"(cos<{threshold:.2f} to {framework!r} prototype, "
                f"provider={provider}, sample={dropped_samples})"
            )
        else:
            logger.info(
                f"[off-topic-filter] kept all {len(entries)} docs "
                f"(provider={provider})"
            )
        return kept
    except Exception as e:
        logger.warning(
            f"[off-topic-filter] failed ({type(e).__name__}: {e}); "
            f"keeping all entries"
        )
        return entries


# =============================================================================
# Tier 2 #6 — Code-aware near-dup detection (2026-04-24)
# =============================================================================
# Two docs that share ~80% prose but differ in code are NOT duplicates
# (common in API docs: `tutorial.md` vs `reference.md` — same overview,
# different imports/examples). Classical MinHash or SemDeDup would drop
# one, silently deleting the code delta.
#
# This implementation:
#   1. Extracts prose + code_blocks per file (re-uses `_bm25_extract_fields`)
#   2. Computes prose shingles (k=5 word shingles, hashed) → Jaccard est.
#   3. Hashes each code block (sha256 over whitespace-normalized content)
#      → set equality check
#   4. A pair is a duplicate iff `prose_jaccard > 0.85 AND code_sets == code_sets`
#   5. On match: keep the LONGER doc (more authoritative content)
#
# No new deps — hand-rolled shingling is plenty for <500 files/chapter.
# Defensive: on any exception, returns the input unchanged.
_MINHASH_SHINGLE_K = 5           # 5-word shingles: long enough to be discriminating
_MINHASH_JACCARD_THRESHOLD = 0.85  # per roadmap #6
_MINHASH_PROSE_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]+")


def _code_block_hash(code: str) -> str:
    """
    Canonicalize a code block and return its sha256 hex digest.
    Canonicalization: strip trailing whitespace on each line, drop trailing
    blank lines, normalize line endings to \\n. Preserves indentation and
    inline comments (they are semantically meaningful in docs).
    """
    import hashlib
    lines = [ln.rstrip() for ln in code.replace("\r\n", "\n").split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    canon = "\n".join(lines).encode("utf-8")
    return hashlib.sha256(canon).hexdigest()


def _prose_shingles(text: str, k: int = _MINHASH_SHINGLE_K) -> set[int]:
    """
    Build a set of hashed k-word shingles over lowercased prose tokens.
    Python's built-in `hash` is salted per-process but consistent within
    a run, which is all we need for Jaccard comparison in-memory.
    """
    toks = [t.lower() for t in _MINHASH_PROSE_TOKEN_RE.findall(text)]
    if len(toks) < k:
        return {hash(" ".join(toks))} if toks else set()
    return {hash(" ".join(toks[i:i + k])) for i in range(len(toks) - k + 1)}


def _dedup_chapter_files(
    entries: list[tuple[str, str]],
    jaccard_threshold: float = _MINHASH_JACCARD_THRESHOLD,
) -> list[tuple[str, str]]:
    """
    Tier 2 #6: drop near-duplicate files where prose matches AND every
    code block matches. The surviving doc is the longer of the pair.

    Algorithm: pairwise compare. For N files, O(N²) comparisons; for N=500
    that's 125K pairs — each comparison is set ops on ~hundreds of hashes,
    so sub-second in practice. Fine for the pre-MAP filter budget.

    Returns a potentially smaller list in the same relative order. On any
    exception, returns input unchanged.
    """
    try:
        if len(entries) < 2:
            return entries
        # Precompute per-file features once
        features: list[tuple[set[int], frozenset[str], int]] = []
        for _, body in entries:
            prose_text, code_text = _bm25_extract_fields(body)
            prose_shingles = _prose_shingles(prose_text)
            # Split code_text back into individual fences for per-block hashes
            code_blocks = _BM25_FENCE_RE.findall(body)
            # findall with groups returns list of tuples (delim, content)
            code_hashes = frozenset(_code_block_hash(blk[1]) for blk in code_blocks)
            features.append((prose_shingles, code_hashes, len(body)))

        # Mark dups via greedy pairwise merge: keep the longer of each pair.
        drop = [False] * len(entries)
        for i in range(len(entries)):
            if drop[i]:
                continue
            pi_sh, pi_code, pi_len = features[i]
            if not pi_sh:
                continue
            for j in range(i + 1, len(entries)):
                if drop[j]:
                    continue
                pj_sh, pj_code, pj_len = features[j]
                if not pj_sh:
                    continue
                # Code sets must be equal — even one different block defeats dup.
                if pi_code != pj_code:
                    continue
                # Prose Jaccard estimate over hashed shingles.
                inter = len(pi_sh & pj_sh)
                union = len(pi_sh | pj_sh)
                if union == 0:
                    continue
                if (inter / union) < jaccard_threshold:
                    continue
                # Dup found — drop the shorter one.
                if pi_len >= pj_len:
                    drop[j] = True
                else:
                    drop[i] = True
                    break  # i is now dropped; no point comparing further
        kept = [e for e, d in zip(entries, drop) if not d]
        return kept
    except Exception as e:  # pragma: no cover — defensive fallback
        logger.warning(f"[dedup] failed ({e}); keeping all entries")
        return entries


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


def _deterministic_linter(chapters: list[tuple[int, str, str]]) -> list[str]:
    """Callers now pass `(number, title, body)` 3-tuples from `_load_all_chapters`.
    Normalize to `(number, body)` internally so the rest of this function stays
    unchanged. Title is only used by the citation scan, not here."""
    chapters = [(n, b) for n, _t, b in chapters] if chapters and len(chapters[0]) == 3 else chapters
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
    chapters: list[tuple[int, str, str]] | list[tuple[int, str]],
    max_terms: int = 12) -> list[str]:
    # Callers now pass (number, title, body) 3-tuples from _load_all_chapters.
    # Project to (number, body) 2-tuples so the rest stays unchanged.
    if chapters and len(chapters[0]) == 3:
        chapters = [(n, b) for n, _t, b in chapters]
    """
    Tier 2 #7 (2026-04-23): TF-IDF glossary extraction across ALL chapters.
    Replaces the chapter-0-only CamelCase heuristic that missed terminology
    introduced in later chapters.

    Strategy:
      1. Treat each chapter body as a document.
      2. Keep only identifier-shaped tokens (CamelCase / snake_case /
         dotted.paths) — these are the API/domain vocabulary.
      3. Compute TF-IDF across chapters; top-N scores = most-distinctive
         terms (appearing across multiple chapters but not so common as
         to be generic).
      4. Return top N unique terms. Falls back to the chapter-0 counter
         heuristic on any failure (defensive).
    """
    if not chapters:
        return []
    import re as _re
    # Token filter — only identifier-shaped strings matter for glossary
    token_re = _re.compile(
        r"\b([A-Z][a-zA-Z0-9]+(?:[A-Z][a-zA-Z0-9]+)+|[a-z]+(?:_[a-z0-9]+){1,})\b"
    )

    def _extract_tokens(text: str) -> str:
        return " ".join(token_re.findall(text))

    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        # Pre-tokenize: documents are whitespace-joined identifier tokens
        # per chapter. TF-IDF uses default whitespace tokenizer on these.
        docs = [_extract_tokens(body) for _, body in chapters]
        # Drop empty docs — sklearn crashes on empty corpus
        docs = [d for d in docs if d.strip()]
        if not docs:
            raise ValueError("no identifier tokens across chapters")
        vec = TfidfVectorizer(
            lowercase = False,  # preserve CamelCase
            token_pattern = r"\S+",  # whitespace-split; we pre-tokenized
            min_df = 1,
            max_df = 0.9,  # drop terms in >90% of chapters (too generic)
        )
        X = vec.fit_transform(docs)
        # Rank terms by max TF-IDF score across all docs (captures
        # "term distinctive to any chapter", which is what we want)
        import numpy as np
        scores = X.max(axis = 0).toarray().flatten()
        terms = vec.get_feature_names_out()
        ranked = sorted(
            zip(terms, scores),
            key = lambda x: x[1],
            reverse = True,
        )
        return [t for t, _ in ranked[:max_terms]]
    except Exception as e:
        # Fallback: chapter-0 counter heuristic
        logger.info(
            f"[glossary] TF-IDF extraction failed ({e}); "
            f"falling back to chapter-0 counter"
        )
        first_content = chapters[0][1]
        from collections import Counter
        counts = Counter(token_re.findall(first_content))
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
async def _invoke_structured_with_fallback(
    *,
    prompt,
    llm,
    schema,
    invoke_vars: dict,
    label: str,
    langfuse_metadata: dict | None = None,
    langfuse_tags: list[str] | None = None,
    langfuse_session_id: str | None = None,
    langfuse_user_id: str | None = None,
    langfuse_run_name: str | None = None):
    """
    Invoke `prompt | model.with_structured_output(schema)` across a
    RunnableWithFallbacks chain, treating a None result as an escalation
    signal (same as a raised exception).

    Why this exists:
      LangChain's RunnableWithFallbacks only retries on raised exceptions.
      `with_structured_output(method="function_calling")` can return None
      when the model responds without a tool_call (plain-text apology,
      malformed tool arguments filtered out by the parser, etc.) — from
      LangChain's perspective that is a successful invocation, so no
      fallback fires and None propagates to the caller. We unpack
      `runnable` + `fallbacks` and walk the models manually so a None
      at any step escalates to the next model, matching the intent
      documented in _synthesize_attempt / _grade_attempt.
    """
    # Tier 3 #14 + 0d-5 (2026-04-23): LangFuse telemetry. The LiteLLM
    # Router behind `llm` handles provider cascade + fail-fast + cooldown
    # internally — we don't iterate models here anymore. Caller gets ONE
    # ainvoke() that walks healthy deployments and auto-cools down the
    # broken ones via Redis TTL cache.
    #
    # What this wrapper still does:
    #   1. Attaches LangFuse callback for per-request tracing
    #   2. Outer timeout belt-and-suspenders (router's own timeouts +
    #      our eager 120s cap per-deployment — router already enforces
    #      per-entry timeouts defined in llm_chain.py)
    #   3. None-guard for the LangChain `with_structured_output` quirk:
    #      `method="function_calling"` can return None when a provider
    #      emits a tool_call the parser rejects. Router's pre-call
    #      checks can't know this; we still need to raise so the caller
    #      (Self-Refine loop) treats it as a failure, not a success.
    from services.knowledge.langfuse_client import langfuse_config
    # 2026-04-24: raised 600 → 1200 after Run-8 evidence showed 6/9 chapters
    # hitting the outer timeout mid-cascade. With per-entry timeouts
    # now uniformly capped at 120s in llm_chain.py, 10 cold entries = 1200s
    # worst case (matching this budget). Realistically most cascades complete
    # well under 300s.
    OUTER_TIMEOUT_SECONDS = 1200

    per_attempt_config = langfuse_config(
        metadata = {**(langfuse_metadata or {}), "label": label},
        tags = [label.split()[0], *(langfuse_tags or [])],
        session_id = langfuse_session_id,
        user_id = langfuse_user_id,
        run_name = langfuse_run_name or f"kd-{label.replace(' ', '-')}",
    )

    try:
        chain = prompt | llm.with_structured_output(
            schema,
            method = "function_calling",
        )
        result = await asyncio.wait_for(
            chain.ainvoke(
                invoke_vars,
                config = per_attempt_config or None,
            ),
            timeout = OUTER_TIMEOUT_SECONDS,
        )
        if result is None:
            raise RuntimeError(
                f"[{label}] LiteLLM Router returned None (all healthy models "
                f"emitted non-parseable output). Triggers Self-Refine retry."
            )
        return result
    except asyncio.TimeoutError as e:
        raise RuntimeError(
            f"[{label}] exceeded outer {OUTER_TIMEOUT_SECONDS}s timeout — "
            f"LiteLLM Router exhausted its cascade or got stuck. "
            f"Last exception: {e}"
        ) from e


# =============================================================================
# Tier 1 #1 — BM25F two-field file ranking (2026-04-24 upgrade)
# =============================================================================
# Original BM25 (2026-04-23) tokenized whole-file text uniformly, which lets
# code keywords (`SELECT`, `import`, `function`) dominate or vanish depending
# on tokenizer choices. BM25F splits each file into `prose` and `code` fields
# and scores them separately with different weights, then sums. Mixed
# prose/code corpora benefit per Turnbull 2025 (softwaredoug.com) and the
# BM25F-from-scratch writeup.
#
# Field weights:
#   - prose: 1.0 (standard tokenizer, stopword-stripped)
#   - code : 0.3 (code-aware tokenizer splits camelCase / snake_case / dots;
#                 preserves identifier bigrams so `foo.bar` → {foo, bar, foo.bar})
#
# Hand-rolled — <500 files per chapter is tiny for BM25; no new dep.
# Falls back to the caller's original order on any exception (defensive).
_BM25_TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_]{1,}")
# Minimal English stopwords — these add noise to chapter.goal matching.
# Kept small; we don't want to strip domain terms like `client` / `server`.
_BM25_STOPWORDS = frozenset({
    "a", "an", "the", "this", "that", "these", "those",
    "is", "are", "was", "were", "be", "been", "being",
    "of", "in", "on", "at", "to", "for", "with", "from",
    "and", "or", "but", "if", "then", "else", "so",
    "do", "does", "did", "have", "has", "had",
    "it", "its", "they", "them", "their", "there",
    "by", "as", "not", "no", "yes",
    "learn", "use", "using",  # chapter.goal often starts with these
})
# Matches the OPENING of a CommonMark fenced code block: ``` or ~~~ at
# line-start with optional info string. Tilde variant (~~~) per CommonMark §4.5.
_BM25_FENCE_RE = re.compile(r"^(```|~~~)[^\n]*\n(.*?)\n\1", re.MULTILINE | re.DOTALL)
# Code-field tokenizer: camelCase/snake_case/dotted-identifier splitter that
# ALSO preserves dotted bigrams. `foo.bar_baz` → foo, bar, baz, foo.bar, bar_baz, foo.bar_baz
_BM25_CODE_CAMEL_RE = re.compile(r"[a-z0-9]+|[A-Z][a-z0-9]*|[A-Z]+(?=[A-Z][a-z]|$)")
_BM25_CODE_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")


def _bm25_tokenize(text: str) -> list[str]:
    """Prose tokenizer: lowercase alphanumeric ≥ 2 chars, stopwords dropped."""
    tokens = [t.lower() for t in _BM25_TOKEN_RE.findall(text)]
    return [t for t in tokens if t not in _BM25_STOPWORDS]


def _bm25_tokenize_code(text: str) -> list[str]:
    """
    Code-aware tokenizer: keeps full dotted identifiers (`foo.bar`) AND
    splits camelCase / snake_case / dot-separated parts into individual
    tokens. Stopword filter not applied — code tokens like `if` / `do` /
    `in` / `from` are syntactically significant.
    """
    out: list[str] = []
    for ident in _BM25_CODE_IDENT_RE.findall(text):
        # full identifier as one token
        out.append(ident.lower())
        # split on dots + underscores into parts, then camelCase on each part
        for part in re.split(r"[._]+", ident):
            if not part:
                continue
            out.append(part.lower())
            sub = _BM25_CODE_CAMEL_RE.findall(part)
            if len(sub) > 1:
                for s in sub:
                    if len(s) >= 2:
                        out.append(s.lower())
    # dedupe while preserving order (first occurrence wins for TF counting)
    seen: set[str] = set()
    return [t for t in out if not (t in seen or seen.add(t))]


def _bm25_extract_fields(body: str) -> tuple[str, str]:
    """
    Split a markdown file into (prose_text, code_text). Preserves the full
    content across the two fields — every char from `body` appears in
    exactly one of them. Used by BM25F to score fields independently.
    """
    code_parts: list[str] = []
    prose_parts: list[str] = []
    last = 0
    for m in _BM25_FENCE_RE.finditer(body):
        prose_parts.append(body[last:m.start()])
        code_parts.append(m.group(2))  # fence body (between delimiters)
        last = m.end()
    prose_parts.append(body[last:])
    return ("".join(prose_parts), "\n".join(code_parts))


def _rank_files_by_bm25(
    goal: str,
    files: list[tuple[str, str]],  # (slug, body)
    k1: float = 1.5,
    b: float = 0.75,
    w_prose: float = 1.0,
    w_code: float = 0.3,
) -> list[tuple[str, str]]:
    """
    BM25F two-field ranker. Returns `files` sorted by combined field score
    against `goal`, highest first. On any exception, returns input unchanged
    (defensive — never let ranker bugs kill a chapter).

    Name retained as `_rank_files_by_bm25` for backward compat with existing
    call sites; implementation is BM25F per Tier 1 #1 (2026-04-24).
    """
    try:
        if len(files) <= 1:
            return files
        prose_q = _bm25_tokenize(goal)
        code_q = _bm25_tokenize_code(goal)
        if not prose_q and not code_q:
            return files

        # Extract + tokenize per field
        prose_docs: list[list[str]] = []
        code_docs: list[list[str]] = []
        for _, body in files:
            prose_text, code_text = _bm25_extract_fields(body)
            prose_docs.append(_bm25_tokenize(prose_text))
            code_docs.append(_bm25_tokenize_code(code_text) if code_text else [])

        prose_lens = [len(d) for d in prose_docs]
        code_lens = [len(d) for d in code_docs]
        avg_prose = (sum(prose_lens) / len(prose_lens)) if prose_lens else 0.0
        avg_code = (sum(code_lens) / len(code_lens)) if code_lens else 0.0
        if avg_prose == 0 and avg_code == 0:
            return files

        import math
        from collections import Counter
        N = len(files)

        def _field_bm25(docs: list[list[str]], lens: list[int], avg_dl: float,
                        query: list[str]) -> list[float]:
            """Classical BM25 over one field. Returns per-doc score list."""
            if avg_dl == 0 or not query:
                return [0.0] * N
            df: dict[str, int] = {}
            for tokens in docs:
                for term in set(tokens):
                    df[term] = df.get(term, 0) + 1
            idf = {
                term: math.log(1 + (N - df[term] + 0.5) / (df[term] + 0.5))
                for term in df
            }
            scores = [0.0] * N
            for i, tokens in enumerate(docs):
                tf = Counter(tokens)
                dl = lens[i]
                norm_dl = 1 - b + b * (dl / avg_dl)
                s = 0.0
                for qt in set(query):
                    if qt not in tf:
                        continue
                    freq = tf[qt]
                    s += idf.get(qt, 0.0) * (freq * (k1 + 1)) / (freq + k1 * norm_dl)
                scores[i] = s
            return scores

        prose_scores = _field_bm25(prose_docs, prose_lens, avg_prose, prose_q)
        code_scores = _field_bm25(code_docs, code_lens, avg_code, code_q)
        combined = [
            (i, w_prose * prose_scores[i] + w_code * code_scores[i])
            for i in range(N)
        ]
        combined.sort(key = lambda x: x[1], reverse = True)
        return [files[i] for i, _ in combined]
    except Exception as e:  # pragma: no cover — defensive fallback
        logger.warning(f"[bm25f] ranking failed ({e}); keeping input order")
        return files


def _tiktoken_count(text: str) -> int:
    """
    Count tokens via cl100k_base (OpenAI's GPT-4 tokenizer). Safe upper bound
    for every model in the fallback chain — Qwen/GLM/Kimi/Llama/DeepSeek all
    tokenize more efficiently than cl100k, so a 40K cl100k count becomes
    ~30-36K real tokens on the wire. Local import + module-level cache keeps
    the first call fast and subsequent calls sub-millisecond.
    """
    import tiktoken
    enc = _tiktoken_count._enc if hasattr(_tiktoken_count, "_enc") else None
    if enc is None:
        enc = tiktoken.get_encoding("cl100k_base")
        _tiktoken_count._enc = enc
    return len(enc.encode(text))


def _fence_safe_split(content: str) -> list[str]:
    """
    Split a markdown document on H1/H2/H3 boundaries that are NOT inside
    fenced code blocks. Used by `_load_chapter_files` when a single assigned
    file alone exceeds the token budget — we pack as many sections as fit,
    never mid-fence.

    Delegates to `services.knowledge.post_ingest._split_markdown_by_headings`
    so both monolith-split and chapter-pack share one token-aware splitter.
    Code fences, tables, list blocks, and HTML blocks are atomic tokens in
    markdown-it-py — they cannot be bisected by walking heading_open
    boundaries.
    """
    from services.knowledge.post_ingest import _split_markdown_by_headings
    sections = _split_markdown_by_headings(content, split_levels = (1, 2, 3))
    return [body for _, _, body in sections if body.strip()]


async def _load_chapter_files(
    storage: MinIOStudyStorage,
    study_root: str,
    slugs: list[str],
    chapter_goal: str | None = None) -> str:
    """
    Concatenate raw file content for a chapter, labeled by slug, token-capped
    at CHAPTER_FILES_MAX_TOKENS. Returns text formatted for SYNTHESIZER_PROMPT's
    {assigned_files_content} placeholder.

    Packing strategy (Tier 1 #2, 2026-04-23; Tier 1 #1 added 2026-04-23):
      0. If `chapter_goal` is provided, rank files by BM25 against the goal
         before packing so the 40K token budget is spent on the most
         topically relevant files. Falls back to planner order on any
         ranker exception. (Tier 1 #1.)
      1. Iterate files in (ranked or planner) order.
      2. For each file: count tokens BEFORE appending (fixes the cap-after-
         append off-by-one that let ch02 get a 10 MB single file under a
         180K-char cap).
      3. If the whole file fits the remaining budget → append whole.
      4. If it doesn't fit but is the FIRST file of the chapter (current
         total == 0) and too large on its own → fence-safely split it into
         H1/H2/H3 sections and greedy-pack sections until budget. Never
         truncates inside a fenced code block.
      5. If it doesn't fit and we already have content → stop iterating.
    """
    # Tier 1 #1: pre-read all files so we can rank them before packing.
    # Previously we read lazily per-iter; reading upfront costs the same
    # (MinIO reads are parallel under _read_many) and unlocks BM25.
    preloaded: list[tuple[str, str]] = []
    for slug in slugs:
        key = f"{study_root}/research/raw/{slug}.md"
        try:
            body = (await storage.read_text(key)).strip()
            preloaded.append((slug, body))
        except Exception as e:
            logger.warning(f"[synth] could not read {key}: {e}")
            continue

    if chapter_goal and preloaded:
        ranked = _rank_files_by_bm25(chapter_goal, preloaded)
        if ranked and [s for s, _ in ranked] != [s for s, _ in preloaded]:
            logger.info(
                f"[synth] BM25-ranked {len(preloaded)} files for goal "
                f"{chapter_goal[:60]!r}; top-3: "
                f"{[s for s, _ in ranked[:3]]}"
            )
        preloaded = ranked

    sections: list[str] = []
    total_tokens = 0
    skipped = 0

    for idx, (slug, body) in enumerate(preloaded):
        remaining = CHAPTER_FILES_MAX_TOKENS - total_tokens
        if remaining <= 0:
            skipped = len(preloaded) - idx
            break

        header = f"--- {slug}.md ---\n"
        section_text = header + body + "\n"
        section_tokens = _tiktoken_count(section_text)

        if section_tokens <= remaining:
            # Whole file fits in the remaining budget.
            sections.append(section_text)
            total_tokens += section_tokens
            continue

        if total_tokens == 0:
            # First file and too big by itself — fence-safe split + pack.
            try:
                chunks = _fence_safe_split(body)
            except Exception as e:
                logger.warning(
                    f"[synth] fence-safe split failed for {slug!r} ({e}); "
                    f"skipping whole file"
                )
                skipped = len(preloaded) - idx
                break
            packed_chunks: list[str] = []
            packed_tokens = _tiktoken_count(header)
            for chunk in chunks:
                chunk_tokens = _tiktoken_count(chunk + "\n\n")
                if packed_tokens + chunk_tokens > remaining:
                    break
                packed_chunks.append(chunk)
                packed_tokens += chunk_tokens
            if packed_chunks:
                sections.append(header + "\n\n".join(packed_chunks) + "\n")
                total_tokens += packed_tokens
                logger.info(
                    f"[synth] file {slug!r} too large (~{section_tokens} tok "
                    f"> {remaining} tok budget) — fence-split into {len(chunks)} "
                    f"sections, packed {len(packed_chunks)} "
                    f"(~{packed_tokens} tok). Remaining {len(preloaded) - idx - 1} "
                    f"file(s) skipped."
                )
                skipped = len(preloaded) - idx - 1
                break
            # Even one section doesn't fit — the corpus is pathologically
            # large. Emit an empty result and let the synth hit an empty
            # prompt; Self-Refine will bail out cleanly.
            logger.warning(
                f"[synth] file {slug!r}: no section fits within "
                f"{remaining} tok budget — chapter will synth on empty corpus"
            )
            skipped = len(preloaded) - idx
            break

        # Not the first file, and it doesn't fit. Stop here rather than
        # silently truncating — planner-assigned files come in priority
        # order, so the later ones can wait for the next run.
        skipped = len(preloaded) - idx
        break

    if skipped > 0:
        logger.info(
            f"[synth] budget cap hit at {total_tokens} tok; "
            f"packed {len(sections)} of {len(preloaded)} files, skipped {skipped}"
        )
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
    llm,
    iteration: int | None = None,
    study_id: str | None = None,
    user_id: str | None = None):
    """
    Single synthesis attempt — Tier 3 #21 structured output.

    Returns a ChapterOutput (sections + challenges + flashcards). The caller
    audits `code_refs` against the vault, then assembles final markdown via
    `_assemble_chapter_markdown`.

    Escalates across the fallback chain via `_invoke_structured_with_fallback`
    so a model that returns no tool_call is treated the same as one that raised.
    """
    return await _invoke_structured_with_fallback(
        prompt = SYNTHESIZER_PROMPT,
        llm = llm,
        schema = ChapterOutput,
        invoke_vars = {
            "framework": framework,
            "chapter_number": chapter.number,
            "chapter_title": chapter.title,
            "chapter_goal": chapter.goal,
            "assigned_files_content": files_content,
            "tone_block": tone_block,
            "previous_adjustments": _format_adjustments(previous_adjustments),
        },
        label = f"synth ch{chapter.number:02d}",
        langfuse_metadata = {
            "framework": framework,
            "chapter_number": str(chapter.number),
            "iteration": str(iteration),
        },
        langfuse_tags = [f"ch{chapter.number:02d}", "synth"],
        langfuse_session_id = study_id,
        langfuse_user_id = user_id,
        langfuse_run_name = f"kd-synth-ch{chapter.number:02d}-iter{iteration}",
    )


# OP-46 (2026-04-25, post-Run-12) — prose-only synth helper for chapters
# whose source files have ZERO fenced code blocks. Bypasses code_refs entirely;
# returns a ProseChapterOutput which is then assembled by
# _assemble_prose_chapter_markdown into a code-free chapter README.
async def _synthesize_prose_attempt(
    chapter: ChapterPlan,
    files_content: str,
    framework: str,
    tone_block: str,
    previous_adjustments: list[str],
    llm,
    iteration: int | None = None,
    study_id: str | None = None,
    user_id: str | None = None) -> ProseChapterOutput:
    """
    Single prose-only synthesis attempt. Used by `synthesize_chapter` when
    `len(code_vault) == 0`. Returns a `ProseChapterOutput` (sections w/
    heading + prose_md only, NO code_refs); caller assembles the markdown
    via `_assemble_prose_chapter_markdown`.
    """
    return await _invoke_structured_with_fallback(
        prompt = SYNTHESIZER_PROSE_PROMPT,
        llm = llm,
        schema = ProseChapterOutput,
        invoke_vars = {
            "framework": framework,
            "chapter_number": chapter.number,
            "chapter_title": chapter.title,
            "chapter_goal": chapter.goal,
            "assigned_files_content": files_content,
            "tone_block": tone_block,
            "previous_adjustments": _format_adjustments(previous_adjustments),
        },
        label = f"synth-prose ch{chapter.number:02d}",
        langfuse_session_id = study_id,
        langfuse_user_id = user_id,
        langfuse_run_name = f"kd-synth-prose-ch{chapter.number:02d}-iter{iteration}",
        langfuse_metadata = {
            "framework": framework,
            "chapter_number": str(chapter.number),
            "iteration": str(iteration),
            "prose_only": "true",
        },
        langfuse_tags = [f"ch{chapter.number:02d}", "synth", "prose-only"],
    )


def _assemble_prose_chapter_markdown(
    output,  # ProseChapterOutput
    chapter_title: str | None = None,
) -> str:
    """
    OP-46 — assemble prose-only chapter (no fenced code blocks). Mirrors
    `_assemble_chapter_markdown` shape but emits ONLY heading + prose.
    Runs through the same `_scrub_assembled_markdown` post-processor for
    Mintlify orphans / fence balance / inline-backtick sanity.
    """
    parts: list[str] = []
    if chapter_title:
        parts.append(f"# {chapter_title}\n")
    for section in output.sections:
        heading = (section.heading or "").strip()
        if heading:
            parts.append(f"## {heading}\n")
        prose = section.prose_md.strip()
        if prose:
            parts.append(prose + "\n")
    assembled = "\n".join(parts).rstrip() + "\n"
    cleaned, scrub_stats = _scrub_assembled_markdown(assembled)
    if any(scrub_stats.values()):
        logger.info(
            "[assembler-prose] scrub applied: "
            f"raw_corpus_boundaries={scrub_stats['raw_corpus_boundaries']} "
            f"mintlify_orphans={scrub_stats['mintlify_orphans']} "
            f"mintlify_fence_meta={scrub_stats['mintlify_fence_meta']} "
            f"fence_balance_fixed={scrub_stats['fence_balance_fixed']} "
            f"inline_fence_sanitized={scrub_stats['inline_fence_sanitized']} "
            f"stacked_citations_split={scrub_stats['stacked_citations_split']} "
            f"literal_newlines_fixed={scrub_stats['literal_newlines_fixed']} "
            f"citation_extensions_stripped={scrub_stats['citation_extensions_stripped']} "
            f"truncated_blocks_repaired={scrub_stats['truncated_blocks_repaired']}"
        )
    return cleaned


def _deterministic_grader_gates(
    synthesis_text: str,
    chapter: ChapterPlan,
) -> tuple[bool, str, dict[str, float]]:
    """
    Tier 2 #9 + Tier 4 #18 — deterministic pre-gates on the grader.

    Runs a handful of cheap heuristics that don't need an LLM call. Returns
    `(pass, reason, partial_scores)`:
      - `pass=False` → skip the grader LLM entirely, fail the iteration
        straight to refine with `reason` as targeted feedback. Saves the
        cascade from burning a grader slot on an obviously-broken chapter.
      - `pass=True` → proceed to the full grader LLM call.
      - `partial_scores` is a dict of deterministic dimension scores that
        the caller can fold into GraderEvaluation for cheap dimensions
        (citation_integrity, code_density) so the LLM only has to judge the
        subjective ones.

    Checks:
      - prose length is within plausible range (100 chars < N < 500K)
      - at least one `# docs:` citation exists (citation_integrity hard floor)
      - at least one fenced code block exists (code_density hard floor)
      - no obvious stub markers (TODO, TBD, PLACEHOLDER as whole words)

    Each gate's failure is a separate branch with its own feedback string;
    all are cheap string ops.
    """
    partial: dict[str, float] = {}
    # length sanity
    if len(synthesis_text) < 500:
        return (False,
                f"Chapter body is only {len(synthesis_text)} chars — way below any "
                f"viable chapter length. Regenerate with real content.", partial)
    if len(synthesis_text) > 500_000:
        return (False,
                f"Chapter body is {len(synthesis_text)} chars — exceeds plausible bounds. "
                f"The synthesizer likely leaked source material verbatim. Rewrite.", partial)
    # citation presence — hard floor on citation_integrity
    citation_count = len(re.findall(r"#\s*docs:\s*[\w/.\-]+", synthesis_text))
    if citation_count == 0:
        partial["citation_integrity"] = 0.0
        return (False,
                f"ZERO `# docs:` citations in chapter {chapter.number}. Every "
                f"non-trivial claim must cite a source slug from: "
                f"{', '.join(chapter.assigned_files[:5])}{'...' if len(chapter.assigned_files) > 5 else ''}. "
                f"Refine with full citation coverage.", partial)
    partial["citation_integrity"] = min(1.0, citation_count / max(1, len(chapter.assigned_files)))
    # fenced-code presence — hard floor on code_density
    fence_count = len(re.findall(r"^(```|~~~)", synthesis_text, re.MULTILINE)) // 2
    if fence_count == 0:
        partial["code_density"] = 0.0
        return (False,
                f"ZERO fenced code blocks in chapter {chapter.number}. For a "
                f"technical-docs synthesis this is a structural failure — "
                f"every section should be code-first. Regenerate.", partial)
    # rough code_density estimate: lines inside fences vs total non-blank lines
    non_blank_lines = sum(1 for line in synthesis_text.split("\n") if line.strip())
    code_lines = 0
    in_fence = False
    for line in synthesis_text.split("\n"):
        if re.match(r"^(```|~~~)", line):
            in_fence = not in_fence
            continue
        if in_fence and line.strip():
            code_lines += 1
    partial["code_density"] = (code_lines / non_blank_lines) if non_blank_lines else 0.0
    # stub markers — hard-fail on explicit "TODO" / "PLACEHOLDER" text
    stub_re = re.compile(r"\b(TODO|TBD|PLACEHOLDER|FIXME|XXX)\b(?![^`]*`)", re.MULTILINE)
    stubs = stub_re.findall(synthesis_text)
    if len(stubs) > 2:
        return (False,
                f"Chapter contains {len(stubs)} stub markers ({', '.join(sorted(set(stubs))[:3])}). "
                f"LLM left placeholders instead of writing real content. Refine with "
                f"fully-fleshed-out prose.", partial)
    return (True, "", partial)


async def _grade_attempt(
    synthesis_text: str,
    chapter: ChapterPlan,
    user_profile: UserProfile,
    framework: str,
    llm,
    iteration: int | None = None,
    study_id: str | None = None,
    user_id: str | None = None,
    audit_summary: str | None = None) -> GraderEvaluation:
    """
    Run the 8-dimensional adaptive grader on one synthesis attempt. Returns
    structured GraderEvaluation with per-dimension scores, a weighted_score,
    an action ('accept' | 'refine' | 'regenerate'), and a list of specific
    issues to address on the next attempt. Escalates across the fallback
    chain via `_invoke_structured_with_fallback`.

    Tier 2 #9 / Tier 4 #18 (2026-04-24): runs cheap deterministic gates first.
    If a gate hard-fails, returns a synthetic GraderEvaluation with
    action="refine" and the gate's reason as a specific_issue — skips the
    LLM call entirely. Saves ~50% of iter-0 grader cascades on obviously
    broken chapters and gives the synthesizer immediate targeted feedback.
    """
    passed, reason, partial = _deterministic_grader_gates(synthesis_text, chapter)
    if not passed:
        from schemas.knowledge.agents import Issue
        logger.warning(
            f"[grader][ch{chapter.number:02d}] deterministic pre-gate FAILED "
            f"(skipping grader LLM): {reason[:160]}"
        )
        return GraderEvaluation(
            signal_to_noise = 0.0,
            assumption_match = 0.0,
            job_alignment = 0.0,
            citation_integrity = partial.get("citation_integrity", 0.0),
            code_density = partial.get("code_density", 0.0),
            portfolio_synergy = 0.0,
            complexity_appropriate = 0.0,
            market_analysis = 0.0,
            code_preservation_ratio = 1.0,  # deterministic audit already passed if we got here
            weighted_score = 0.0,
            specific_issues = [
                Issue(
                    span_quote = synthesis_text[:200],
                    dimension = "signal_to_noise",
                    suggestion = reason,
                )
            ],
            action = "refine",
        )
    # Phase 1.3 (2026-05-13): KD_USE_CLASSICAL_GRADER=1 routes the grader
    # through services/knowledge/grader_classical.py — deterministic per-dim
    # scorers + (Phase 1.2) one small-LLM call for the market_analysis dim.
    # Drop-in shape: returns the same GraderEvaluation Pydantic. Default "0"
    # keeps the legacy LLM grader until A/B validation on /debug/grader_compare
    # shows the classical scores agree on existing chapters. See
    # docs/KD-SYNTH-LLM-TO-CLASSICAL-MAY2026.md Phase 1.
    if os.environ.get("KD_USE_CLASSICAL_GRADER", "0").strip().lower() in (
        "1", "true", "yes",
    ):
        from services.knowledge.grader_classical import score_chapter_classically
        result = await score_chapter_classically(
            synthesis_text = synthesis_text,
            chapter = chapter,
            user_profile = user_profile,
            audit_summary = audit_summary or "",
            framework = framework,
        )
        logger.info(
            f"[grader][ch{chapter.number:02d}] classical scored: "
            f"composite={result.weighted_score:.3f} action={result.action} "
            f"issues={len(result.specific_issues)}"
        )
        return result
    return await _invoke_structured_with_fallback(
        prompt = GRADER_PROMPT,
        llm = llm,
        schema = GraderEvaluation,
        invoke_vars = {
            "framework": framework,
            "user_profile_summary": _user_profile_summary(user_profile),
            "acceptance_threshold": user_profile.acceptance_threshold,
            "assigned_files_list": ", ".join(chapter.assigned_files),
            "synthesis_text": synthesis_text[:GRADER_SYNTHESIS_MAX_CHARS],
            # OP-17 (2026-04-25) — pass deterministic audit signals to
            # the grader so it can calibrate borderline accept decisions
            # against verified facts instead of re-deriving them.
            "audit_summary": audit_summary or "(no audit summary provided)",
        },
        label = f"grade ch{chapter.number:02d}",
        langfuse_session_id = study_id,
        langfuse_user_id = user_id,
        langfuse_run_name = f"kd-grade-ch{chapter.number:02d}-iter{iteration}",
        langfuse_metadata = {
            "framework": framework,
            "chapter_number": str(chapter.number),
            "iteration": str(iteration),
        },
        langfuse_tags = [f"ch{chapter.number:02d}", "grader"],
    )


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

    Tier 1 #10 (2026-04-23): to eliminate critic false-positives like
    `# docs: api(utils)` capturing the literal `api` as a slug, we build
    an exact-match whitelist regex from `available_slugs` when non-empty
    and use it as the authoritative scanner. Falls back to the legacy
    greedy `_CITATION_RE` when the slug set is empty (e.g., no corpus
    files, edge case).

    Returns:
        (all_cited_slugs, per_chapter_broken_issues)
        where each broken_issue is a string formatted for CriticAssessment.issues
        like 'chapter03: '# docs: quickstart' — source not found in research/raw/'.
    """
    all_cited: set[str] = set()
    issues: list[str] = []

    # Build a whitelist regex: `# docs: (slug-alternation)` with word-boundary
    # end so `api` doesn't match `api/reference`. Sort by length descending
    # so longer slugs match first (disambiguates nested slug families).
    if available_slugs:
        sorted_slugs = sorted(available_slugs, key = lambda s: (-len(s), s))
        alt = "|".join(re.escape(s) for s in sorted_slugs)
        whitelist_re = re.compile(
            rf"#\s*docs:\s*({alt})(?:\.md|\.txt)?(?![\w./-])",
            re.MULTILINE,
        )
        for number, _title, body in chapters:
            for match in whitelist_re.finditer(body):
                all_cited.add(match.group(1))
            # Also scan for ATTEMPTED citations that didn't match the
            # whitelist — those are broken references.
            for match in _CITATION_RE.finditer(body):
                raw = match.group(1).strip().rstrip(".,;:)(]}")
                slug = raw.removesuffix(".md").removesuffix(".txt")
                if not slug:
                    continue
                if slug not in available_slugs:
                    issues.append(
                        f"chapter{number:02d}: '# docs: {raw}' — source not "
                        "found in research/raw/"
                    )
        return all_cited, issues

    # Legacy fallback — no slug whitelist available
    for number, _title, body in chapters:
        for match in _CITATION_RE.finditer(body):
            raw = match.group(1).strip().rstrip(".,;:)(]}")
            slug = raw.removesuffix(".md").removesuffix(".txt")
            if not slug:
                continue
            all_cited.add(slug)
            if slug not in available_slugs:
                issues.append(
                    f"chapter{number:02d}: '# docs: {raw}' — source not found in research/raw/"
                )
    return all_cited, issues


# =============================================================================
# OP-59 (2026-04-25, post-Run-13) — tree-sitter code_syntax_valid replacement
# =============================================================================
# Replaces the LLM critic's `code_syntax_valid` score with deterministic
# parse-rate via tree-sitter. The parser is ground truth for "does this
# parse?" — strictly more reliable than LLM judgment which can hallucinate
# false errors on valid code or miss real syntax errors. Free, fast,
# CPU-only via tree-sitter-language-pack (305 languages).
#
# Heuristics:
# - Fence with empty language tag → skip (can't validate)
# - Output-style blocks (no language, body has shell prompt `$ `) → skip
# - Unknown language → skip (don't penalize)
# - Score = parsed / parse_attempted (NOT total fences)
# - Returns 1.0 if no parse-attemptable blocks (vacuously valid)
_TREE_SITTER_LANG_ALIASES = {
    "py": "python", "python3": "python",
    "js": "javascript", "ts": "typescript", "tsx": "tsx", "jsx": "jsx",
    "sh": "bash", "shell": "bash", "zsh": "bash",
    "yml": "yaml",
    "rs": "rust", "go": "go",
    "rb": "ruby",
    "kt": "kotlin",
    "cs": "c_sharp",
    "cpp": "cpp", "cxx": "cpp", "cc": "cpp", "c++": "cpp",
    "hcl": "hcl", "tf": "hcl", "terraform": "hcl",
    "dockerfile": "dockerfile", "docker": "dockerfile",
    "json": "json", "json5": "json",
    "html": "html", "css": "css", "scss": "css",
    "sql": "sql",
    "md": "markdown", "markdown": "markdown",
    "toml": "toml",
    "java": "java",
    "lua": "lua",
}
_TREE_SITTER_FENCE_RE = re.compile(
    r"```([a-zA-Z0-9_+\-]*)\n(.*?)\n```", re.DOTALL,
)


def _compute_code_syntax_valid_score(chapters: list[tuple[int, str, str]]) -> tuple[float, dict]:
    """
    OP-59: deterministic code_syntax_valid score via tree-sitter parse-rate
    across all chapters. Returns (score, stats) where:
      - score: parsed / parse_attempted (1.0 if nothing parse-attemptable)
      - stats: per-language pass/fail counts, total skipped
    """
    stats = {"parsed": 0, "failed": 0, "skipped_no_lang": 0,
             "skipped_unknown_lang": 0, "skipped_output": 0,
             "by_lang": {}}
    try:
        from tree_sitter_language_pack import get_parser  # type: ignore
    except ImportError:
        logger.warning("[critic] tree-sitter-language-pack not installed; "
                       "OP-59 deterministic code_syntax falls back to neutral 1.0")
        return 1.0, stats
    for _num, _title, body in chapters:
        for lang_tag, code in _TREE_SITTER_FENCE_RE.findall(body):
            lang_tag = lang_tag.lower().strip()
            if not lang_tag:
                # Bare ``` — likely shell output, dataframe dump, etc.
                # Heuristic: if first content line starts with `$ ` or
                # numeric prefix, treat as output and skip; otherwise
                # try to detect language from content.
                first_line = code.lstrip().split("\n", 1)[0]
                if first_line.startswith(("$", "#", "%", ">>>", "...")):
                    stats["skipped_output"] += 1
                    continue
                stats["skipped_no_lang"] += 1
                continue
            lang = _TREE_SITTER_LANG_ALIASES.get(lang_tag, lang_tag)
            try:
                parser = get_parser(lang)
            except Exception:
                stats["skipped_unknown_lang"] += 1
                continue
            try:
                tree = parser.parse(code.encode("utf-8"))
                ok = not tree.root_node.has_error
            except Exception:
                ok = False
            lang_stats = stats["by_lang"].setdefault(lang, {"parsed": 0, "failed": 0})
            if ok:
                stats["parsed"] += 1
                lang_stats["parsed"] += 1
            else:
                stats["failed"] += 1
                lang_stats["failed"] += 1
    attempted = stats["parsed"] + stats["failed"]
    score = (stats["parsed"] / attempted) if attempted > 0 else 1.0
    return score, stats


def _fence_content_hash(fence_text: str) -> str:
    """
    OP-72B (2026-04-25, post-Run-13) — content-only fence hash.

    Strip the opening line (``` + language tag + attrs) and the closing line
    (```), keep only the body. Hash the body via sha256[:12]. Whitespace-
    normalized: trailing whitespace stripped per line, trailing blank lines
    dropped. Return empty string for malformed fences (no clear body).

    Why content-only: Run-13 ch04 had 9 false-positive "hallucinated" flags
    because the curator's prose-fence concatenation produced different fence
    framing than source had — same code body, different markers. Hashing
    body-only makes hash invariant to fence-marker formatting differences.
    """
    lines = fence_text.split("\n")
    # Strip opening fence line (must start with ``` or ~~~)
    if lines and (lines[0].lstrip().startswith("```") or lines[0].lstrip().startswith("~~~")):
        lines = lines[1:]
    # Strip closing fence line (last non-empty line that's just ``` or ~~~)
    while lines and not lines[-1].strip():
        lines.pop()
    if lines and (lines[-1].strip() == "```" or lines[-1].strip() == "~~~"):
        lines = lines[:-1]
    body = "\n".join(line.rstrip() for line in lines).strip("\n")
    if not body:
        return ""
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]


async def _scan_hallucinated_fences(
    storage: MinIOStudyStorage,
    study_root: str,
    chapters: list[tuple[int, str, str]],
) -> list[str]:
    """
    Tier 2 #20 (2026-04-23) — deterministic end-to-end code-provenance check.

    For every fenced code block in the assembled chapter READMEs, compute
    its sha256[:12] CONTENT hash (OP-72B 2026-04-25 — body only, fence
    markers stripped). Build the union of source-file code-content hashes
    by re-vaulting every research/raw/*.md. Any chapter-fence hash NOT
    present in the source set = hallucinated code (the synthesizer
    invented content that didn't come from the docs).

    Runs AFTER the curator, so it catches late-drift: a clean synth output
    could theoretically get corrupted by a curator rewrite that invents new
    fences. With Tier 3 #21 structured output this should be impossible at
    synth time, but the critic backstop is cheap insurance and runs once
    per study.

    Returns a list of issue strings for CriticAssessment.issues.
    """
    # 1. Union of all source code CONTENT hashes (OP-72B body-only)
    source_hashes: set[str] = set()
    raw_prefix = f"{study_root}/research/raw/"
    raw_keys = await storage.list(raw_prefix)
    for k in sorted(raw_keys):
        if not k.endswith(".md"):
            continue
        try:
            body = await storage.read_text(k)
            _, source_vault = _vault_code_blocks(body)
            for fence_text in source_vault.values():
                h = _fence_content_hash(fence_text)
                if h:
                    source_hashes.add(h)
        except Exception as e:
            logger.warning(f"[critic][fence-scan] could not vault source {k}: {e}")

    # 2. Scan each chapter for fences; report any whose content-hash is not in source
    issues: list[str] = []
    for num, _title, chapter_body in chapters:
        try:
            _, chapter_vault = _vault_code_blocks(chapter_body)
        except ValueError:
            # Body already contains a sentinel (shouldn't happen post-assembly,
            # but defensive — skip rather than crash critic).
            logger.warning(
                f"[critic][fence-scan] ch{num:02d} contains vault-shaped "
                f"sentinels in assembled output; skipping fence scan"
            )
            continue
        for sentinel, fence_text in chapter_vault.items():
            h = _fence_content_hash(fence_text)
            if not h:
                continue  # empty fence body — not actionable
            if h not in source_hashes:
                preview = fence_text[:100].replace("\n", " ⏎ ")
                issues.append(
                    f"chapter{num:02d}: hallucinated code fence (content-hash={h}) "
                    f"not present in any research/raw/ source — preview: {preview!r}"
                )
    return issues


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
    raw_content = response.content if hasattr(response, "content") else response
    if isinstance(raw_content, list):
        parts: list[str] = []
        for block in raw_content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                btype = block.get("type", "")
                if btype in ("text", "output_text") or btype == "":
                    parts.append(str(block.get("text", "")))
        raw_content = "".join(parts)
    elif not isinstance(raw_content, str):
        raw_content = str(raw_content)
    return raw_content.strip()


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

    # --- Section 1: grader debts (chapters below acceptance threshold) ------
    # Partition by `debt.reason` so the two failure modes render with the
    # right fields. `score_below_threshold` carries final_score + threshold +
    # span-anchored specific_issues (grader output). `synth_chain_exhausted`
    # (Tier 0d-6) carries error + iteration_failed_at + counters — there is
    # no final_score because no iteration ever reached a graded state.
    below_threshold = [
        r for r in synthesis_results
        if (r.get("debt") or {}).get("reason", "score_below_threshold")
        == "score_below_threshold"
        and r.get("debt")
    ]
    exhausted = [
        r for r in synthesis_results
        if (r.get("debt") or {}).get("reason") == "synth_chain_exhausted"
    ]
    if below_threshold:
        dirty = True
        lines.append("## Chapters Below Grader Threshold")
        lines.append("")
        for r in below_threshold:
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

    if exhausted:
        dirty = True
        lines.append("## Chapters With Exhausted Synthesis Fallback Chain")
        lines.append("")
        lines.append(
            "The LLM fallback chain was exhausted (every model either raised "
            "or returned None / malformed structured output) OR every "
            "Self-Refine iteration failed the code-preservation integrity "
            "gate. The chapter has no README and will be regenerated on the "
            "next run of the same study identity."
        )
        lines.append("")
        for r in exhausted:
            d = r["debt"]
            lines.append(
                f"- **Chapter {r['number']:02d}** — failed at iter "
                f"{d.get('iteration_failed_at', '?')} "
                f"({d.get('graded_iterations', 0)} graded, "
                f"{d.get('adjustments_accumulated', 0)} adjustment(s) accumulated)"
            )
            err = d.get("error", "")
            if err:
                lines.append(f"  - error: `{err[:200]}`")
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
