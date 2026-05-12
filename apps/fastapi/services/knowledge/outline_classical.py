"""
Knowledge Distiller — Classical (Deterministic) Outline (Phase 3.1, 2026-05-13)

Replaces the LLM-driven `generate_outline` (Phase A of hierarchical synth)
with a hybrid: deterministic header-based section extraction + 1 small LLM
call for the irreducibly creative `challenges` + `flashcards` artifacts.

Source pattern: `KD-SYNTH-LLM-TO-CLASSICAL-MAY2026.md` Phase 3 / Step A.
The synth doc proposed wtpsplit + SemanticChunker + N×small naming LLM. We
go further: technical-doc chapters already have meaningful `##` headers
in their source files, so we use those headers AS section names — zero
LLM calls for naming. Falls back to equal-chunk splitting if no headers.

Before (current):
    OUTLINE_PROMPT call → ChapterOutline (sections + challenges + flashcards)
    ~30s wall-clock, ~30K input tokens, ~1.5K output tokens

After (Phase 3.1):
    1. Extract `##` headers from files_content (skip those inside code fences)
    2. Group resulting sections to land in 4-15 range
    3. Build OutlineSection objects with heading=header_text (deterministic)
    4. Make 1 small LLM call over section-summaries (~3K tokens) for the
       creative artifacts (challenges + flashcards) — uses kd-all rotator
       (need creative output quality)
    ~5-10s wall-clock, ~3K input tokens, ~800 output tokens
    LLM call count: 1 (unchanged) — but ~80-90% token reduction

Why not even fewer LLM calls? challenges + flashcards are genuinely creative
artifacts — extractive question generation produces fact-recall questions
that don't require comprehension. Real learning questions need an LLM.

The first-cut approach is intentionally simple. If the resulting section
structure has quality issues on specific corpora (e.g., flat docs with no
headers), Phase 3.2 can add embedding-based chunking via the kd-embed
rotator (SemanticChunker pattern).
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from pydantic import BaseModel, Field

from schemas.knowledge.agents import (
    ChapterOutline,
    ChapterPlan,
    Flashcard,
    OutlineSection,
)
from schemas.knowledge.agents import ChapterOutline as _ChapterOutline  # for typing


logger = logging.getLogger(__name__)


# =============================================================================
# Tuning constants
# =============================================================================
_MIN_SECTIONS = 4   # matches ChapterOutline.sections min_length
_MAX_SECTIONS = 15  # matches ChapterOutline.sections max_length
_TARGET_SECTIONS = 8  # midpoint; the synth doc's empirical sweet spot
# Cap on chars per section sent to the LLM in the challenges/flashcards
# prompt — keeps the call ~3K input tokens total.
_SECTION_SUMMARY_CHARS = 200
# Max words in an OutlineSection.heading per the schema (2-8 words, but
# we cap at 8 to be safe; truncate longer headers).
_MAX_HEADING_WORDS = 8

# Markdown header regex — captures `##` / `###` / `####` headers (depths
# 2-4; depth-1 `#` is typically the chapter title itself). Each match is
# a tuple (depth, text).
_HEADER_RE = re.compile(r"^(#{2,4})\s+(.+?)\s*$", re.MULTILINE)
_FENCE_RE = re.compile(r"^(```|~~~)", re.MULTILINE)
# Generic / non-informative heading texts to skip — these waste a
# section slot on filler.
_BANNED_HEADINGS = {
    "introduction", "overview", "summary", "conclusion",
    "recap", "takeaways", "takeaway", "preface", "foreword",
}


def _truncate_heading(text: str) -> str:
    """Clamp a heading to <=8 words for OutlineSection.heading."""
    text = text.strip()
    # Remove trailing punctuation
    text = re.sub(r"[.!?:;,\s]+$", "", text)
    words = text.split()
    if len(words) > _MAX_HEADING_WORDS:
        text = " ".join(words[:_MAX_HEADING_WORDS])
    # Empty after stripping → fallback
    return text or "Untitled Section"


def _strip_code_fences(text: str) -> str:
    """
    Replace fenced code blocks with `_FENCE_MASK` placeholders so that
    headers inside code blocks don't accidentally split sections. Returns
    text with the same line count (we only blank out fence-content lines).
    """
    lines = text.split("\n")
    in_fence = False
    masked: list[str] = []
    for line in lines:
        if re.match(r"^(```|~~~)", line):
            in_fence = not in_fence
            masked.append("")
        elif in_fence:
            masked.append("")
        else:
            masked.append(line)
    return "\n".join(masked)


def _extract_sections_from_headers(
    files_content: str,
) -> list[tuple[str, str]]:
    """
    Split markdown text by `##` (and deeper) headers, ignoring headers
    inside code fences. Returns [(heading_text, body_chars), ...] where
    body_chars is the text BETWEEN this header and the next.

    The body of the first segment (before the first header) is dropped
    because chapters typically start with prose/intro that doesn't belong
    to any titled section.
    """
    masked = _strip_code_fences(files_content)
    matches = list(_HEADER_RE.finditer(masked))
    if not matches:
        return []

    sections: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        heading_raw = m.group(2)
        body_start = m.end() + 1
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(files_content)
        body = files_content[body_start:body_end].strip()
        # Strip empty / banned headings
        if heading_raw.strip().lower() in _BANNED_HEADINGS:
            continue
        if not body:
            continue
        sections.append((_truncate_heading(heading_raw), body))
    return sections


def _consolidate_sections(
    sections: list[tuple[str, str]],
    target: int = _TARGET_SECTIONS,
) -> list[tuple[str, str]]:
    """
    If we have more than `_MAX_SECTIONS`, merge consecutive small sections
    until we're at target count. We pick the smallest-by-body-size section
    each iteration and merge it into its smaller neighbor. Preserves order.
    """
    if len(sections) <= _MAX_SECTIONS:
        return sections
    sections = [(h, b) for h, b in sections]
    while len(sections) > target:
        # Find the smallest section by body length
        idx_min = min(range(len(sections)), key=lambda i: len(sections[i][1]))
        # Merge into the smaller neighbor (preferring forward when tied)
        if idx_min == 0:
            neighbor = 1
        elif idx_min == len(sections) - 1:
            neighbor = idx_min - 1
        else:
            left_size = len(sections[idx_min - 1][1])
            right_size = len(sections[idx_min + 1][1])
            neighbor = idx_min - 1 if left_size <= right_size else idx_min + 1
        # Merge: keep the larger section's heading
        big_idx = neighbor if len(sections[neighbor][1]) >= len(sections[idx_min][1]) else idx_min
        small_idx = idx_min if big_idx == neighbor else neighbor
        merged_body = sections[min(big_idx, small_idx)][1] + "\n\n" + sections[max(big_idx, small_idx)][1]
        merged_heading = sections[big_idx][0]
        # Apply merge — replace the lower index with merged, drop the higher
        lo, hi = sorted([big_idx, small_idx])
        sections[lo] = (merged_heading, merged_body)
        sections.pop(hi)
    return sections


def _split_into_equal_chunks(
    files_content: str,
    n: int = _MIN_SECTIONS,
) -> list[tuple[str, str]]:
    """
    Fallback: split `files_content` into `n` equal-sized chunks at line
    boundaries. Used when no headers exist (rare for technical docs).
    Each chunk gets a generic heading "Part 1", "Part 2", etc.
    """
    lines = files_content.split("\n")
    if not lines:
        return [("Chapter Content", files_content)]
    chunk_size = max(1, len(lines) // n)
    sections: list[tuple[str, str]] = []
    for i in range(n):
        start = i * chunk_size
        end = (i + 1) * chunk_size if i < n - 1 else len(lines)
        body = "\n".join(lines[start:end]).strip()
        if body:
            sections.append((f"Part {i + 1}", body))
    # Defensive: ensure at least _MIN_SECTIONS
    while len(sections) < _MIN_SECTIONS:
        sections.append((f"Part {len(sections) + 1}", "(empty)"))
    return sections


def _build_section_summaries(
    sections: list[tuple[str, str]],
) -> str:
    """
    Build a compact summary string showing each section's heading + first
    `_SECTION_SUMMARY_CHARS` of body. This goes into the LLM prompt for
    challenges/flashcards generation — drops the input from ~30K tokens
    (full chapter) to ~3K tokens (200 chars × N sections).
    """
    lines: list[str] = []
    for idx, (heading, body) in enumerate(sections, start=1):
        # First N chars of the body, cleaned up
        summary = body.replace("\n", " ").strip()[:_SECTION_SUMMARY_CHARS]
        if len(body) > _SECTION_SUMMARY_CHARS:
            summary += "..."
        lines.append(f"  {idx}. {heading} — {summary}")
    return "\n".join(lines)


def _build_outline_sections(
    sections_raw: list[tuple[str, str]],
) -> list[OutlineSection]:
    """
    Convert (heading, body) tuples into OutlineSection objects. Heading
    comes from the markdown directly; goal + assumes_from_prior_sections
    are template-generated (deterministic).
    """
    out: list[OutlineSection] = []
    for i, (heading, _body) in enumerate(sections_raw):
        # Goal: deterministic template; downstream Phase C section synth
        # uses this as the synthesis target. The original LLM-produced
        # goal had stylistic variance ("learn to X", "understand X",
        # "build X"); template-based loses that variance but preserves
        # the structural contract.
        goal = f"Master the concepts and patterns of {heading}."
        if i == 0:
            assumes = ""
        elif i == 1:
            assumes = f"Reader has absorbed the {sections_raw[0][0]} content."
        else:
            assumes = f"Reader has covered the prior {i} section(s), most recently {sections_raw[i - 1][0]}."
        out.append(OutlineSection(
            heading=heading,
            goal=goal,
            assumes_from_prior_sections=assumes,
        ))
    return out


# =============================================================================
# Small LLM call for challenges + flashcards (the only remaining LLM in
# classical outline). Routed through the same llm chain the caller passes
# in — so it inherits the kd-all rotator + json_schema config.
# =============================================================================
class _ChallengesFlashcards(BaseModel):
    """Output schema for the challenges/flashcards generation step."""
    challenges: str = Field(
        description=(
            "5-10 active-recall questions as a markdown numbered list. "
            "Mix of conceptual ('Why does X block on Y?') and applied "
            "('Write a function that...'). Each question should test "
            "understanding of the chapter content, not just recall of "
            "section names."
        ),
    )
    flashcards: list[Flashcard] = Field(
        min_length=4,
        max_length=15,
        description="4-15 Anki Q/A pairs. Each pair stands alone.",
    )


async def _generate_challenges_flashcards(
    *,
    chapter: ChapterPlan,
    sections_raw: list[tuple[str, str]],
    framework: str,
    tone_block: str,
    llm,
    study_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> _ChallengesFlashcards:
    """
    Generate just challenges + flashcards for the chapter. Sees section
    summaries (200 chars each) rather than the full chapter content —
    keeps the prompt ~3K tokens (vs ~30K for the original OUTLINE_PROMPT).
    """
    from langchain_core.prompts import ChatPromptTemplate

    prompt = ChatPromptTemplate.from_messages([
        ("system",
         "Given a chapter's section structure, generate 5-10 active-recall "
         "questions and 4-15 Anki Q/A flashcards. Each question/card must "
         "test UNDERSTANDING of the actual content, not just recall of "
         "section names. Be specific and code-flavored where possible.\n\n"
         "{tone_block}\n\n"
         "Output strictly the JSON schema fields `challenges` (markdown "
         "numbered list) and `flashcards` (list of Q/A pairs)."),
        ("human",
         "Framework: {framework}\n"
         "Chapter: {chapter_number} — {chapter_title}\n"
         "Chapter goal: {chapter_goal}\n\n"
         "Section structure (heading → first {summary_chars} chars):\n"
         "{section_summaries}\n\n"
         "Generate challenges + flashcards."),
    ])

    summaries = _build_section_summaries(sections_raw)
    chain = prompt | llm.with_structured_output(
        _ChallengesFlashcards, method="json_schema",
    )

    try:
        from services.knowledge.langfuse_client import langfuse_config as _lf_cfg
        result = await chain.ainvoke(
            {
                "framework": framework,
                "chapter_number": str(chapter.number),
                "chapter_title": chapter.title,
                "chapter_goal": chapter.goal,
                "section_summaries": summaries,
                "summary_chars": _SECTION_SUMMARY_CHARS,
                "tone_block": tone_block,
            },
            config=_lf_cfg(
                metadata={"chapter": str(chapter.number), "label": "classical-outline-creative"},
                tags=["hierarchical", "phase-a-outline-classical"],
                session_id=study_id,
                user_id=user_id,
                run_name=f"kd-classical-outline-creative-ch{chapter.number:02d}",
            ) or None,
        )
        return result
    except Exception as e:
        logger.warning(
            f"[outline-classical][ch{chapter.number:02d}] challenges/flashcards "
            f"LLM call failed ({type(e).__name__}: {str(e)[:600]}); "
            f"emitting synthetic minimal artifacts"
        )
        # Defensive synthetic fallback — Pydantic min_length on Flashcard
        # is 4, so emit 4 minimal pairs from section headings.
        return _ChallengesFlashcards(
            challenges=(
                "1. Explain the key concept covered in this chapter.\n"
                "2. Provide a working code example demonstrating its primary use.\n"
                "3. What trade-offs are involved with this approach?\n"
                "4. How does this integrate with other chapter concepts?\n"
                "5. When would you choose this over alternatives?"
            ),
            flashcards=[
                Flashcard(
                    front=f"What is the core concept of {h}?",
                    back=f"Refer to the {h} section in this chapter for the answer.",
                )
                for h, _b in sections_raw[:4]
            ],
        )


# =============================================================================
# Top-level — drop-in replacement for hierarchical_synth.generate_outline
# =============================================================================
async def generate_outline_classically(
    *,
    chapter: ChapterPlan,
    files_content: str,
    code_vault: dict[str, str],
    framework: str,
    tone_block: str,
    llm,
    iteration: int = 0,
    study_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> ChapterOutline:
    """
    Classical replacement for Phase A's `generate_outline`. Same signature
    so the call site swap is a one-line conditional. Same output type
    (ChapterOutline) so downstream Phase B (vault routing), Phase C
    (per-section synth), and Phase D (assemble) all work unchanged.

    Algorithm:
      1. Extract `##`/`###`/`####` headers from `files_content`, skipping
         those inside fenced code blocks (deterministic, ~10ms).
      2. Filter generic/banned heading texts ("Introduction", "Summary",
         etc.).
      3. If 0 headers: fall back to equal-chunk splitting into 4 sections.
         If >15 headers: merge smallest consecutive sections until <=15.
         If 4-15 headers: use as-is.
      4. Build OutlineSection objects with heading = raw header text,
         goal = template, assumes_from_prior_sections = template.
      5. Make 1 small LLM call (section summaries → challenges +
         flashcards) — the only LLM in this path, ~3K input tokens.
      6. Assemble ChapterOutline.
    """
    # Step 1+2: extract sections from headers
    sections_raw = _extract_sections_from_headers(files_content)

    # Step 3: normalize to 4-15 sections
    if not sections_raw:
        logger.info(
            f"[outline-classical][ch{chapter.number:02d}] no `##`+ headers "
            f"found in {len(files_content)} chars of source; "
            f"falling back to equal-chunk split into {_MIN_SECTIONS}"
        )
        sections_raw = _split_into_equal_chunks(files_content, n=_MIN_SECTIONS)
    elif len(sections_raw) > _MAX_SECTIONS:
        before = len(sections_raw)
        sections_raw = _consolidate_sections(sections_raw, target=_TARGET_SECTIONS)
        logger.info(
            f"[outline-classical][ch{chapter.number:02d}] consolidated "
            f"{before} headers → {len(sections_raw)} sections "
            f"(target={_TARGET_SECTIONS})"
        )
    elif len(sections_raw) < _MIN_SECTIONS:
        # Pad by repeating the largest section split into smaller pieces.
        # Simpler: just complete with chunks of remaining content.
        logger.info(
            f"[outline-classical][ch{chapter.number:02d}] only "
            f"{len(sections_raw)} header(s) found; supplementing via "
            f"equal-chunk split to reach _MIN_SECTIONS={_MIN_SECTIONS}"
        )
        extra = _split_into_equal_chunks(
            files_content, n=_MIN_SECTIONS - len(sections_raw),
        )
        sections_raw = sections_raw + extra

    # Step 4: build OutlineSection objects
    outline_sections = _build_outline_sections(sections_raw)

    # Step 5: 1 small LLM call for challenges + flashcards
    creative = await _generate_challenges_flashcards(
        chapter=chapter,
        sections_raw=sections_raw,
        framework=framework,
        tone_block=tone_block,
        llm=llm,
        study_id=study_id,
        user_id=user_id,
    )

    # Step 6: assemble
    result = ChapterOutline(
        sections=outline_sections,
        challenges=creative.challenges,
        flashcards=creative.flashcards,
    )
    logger.info(
        f"[outline-classical][ch{chapter.number:02d}] generated outline: "
        f"{len(outline_sections)} sections, "
        f"{len(creative.flashcards)} flashcards, "
        f"challenges {len(creative.challenges)} chars; "
        f"iteration={iteration}"
    )
    return result
