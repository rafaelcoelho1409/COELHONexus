"""Pydantic contracts of the planner agents: what each role must answer, validated before any node uses it."""
from __future__ import annotations
from . import params

import logging
import functools
import typing

import pydantic


logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------------- page_judge

class PageRelevance(pydantic.BaseModel):
    """The relevance verdict for one documentation page."""

    model_config = pydantic.ConfigDict(extra = "forbid")

    verdict: typing.Literal["KEEP", "DROP"] = pydantic.Field(
        description = "KEEP for teaching content about the framework; DROP for repository meta-content.",
    )


# ----------------------------------------------------------------------------- page_distiller

# What kind of page this is, in the Diataxis sense plus two roles chapter planning needs: `overview` (landing, index and
# TOC pages that mostly point at other pages) and `cross_cutting` (platform support, debugging, performance, threading or
# compatibility notes that apply across the whole framework, not to one topic).
DocRole = typing.Literal["overview", "cross_cutting", "tutorial", "how_to", "reference", "explanation"]


class DocDistillate(pydantic.BaseModel):
    """Per-doc semantic representation for the LLM-first planner (validated: the shape every later node relies on)."""

    model_config = pydantic.ConfigDict(extra = "forbid")

    summary: str
    key_terms: list[str]
    role: DocRole = params.DEFAULT_ROLE

    @pydantic.field_validator("summary")
    @classmethod
    def _validate_summary(cls, v: str) -> str:
        s = " ".join(v.strip().split())
        n = len(s.split())
        if not (params.SUMMARY_WORDS_MIN <= n <= params.SUMMARY_WORDS_MAX):
            raise ValueError(f"summary must be {params.SUMMARY_WORDS_MIN}-{params.SUMMARY_WORDS_MAX} words; got {n}")
        return s

    @pydantic.field_validator("key_terms")
    @classmethod
    def _validate_terms(cls, v: list[str]) -> list[str]:
        # Repair, don't reject: over-long lists are clipped and out-of-range or duplicate terms dropped; only a genuine
        # shortfall (fewer than KEY_TERMS_MIN usable terms) raises.
        out: list[str] = []
        seen: set[str] = set()
        for t in v:
            s = " ".join(t.strip().split())
            if not (params.KEY_TERM_CHARS_MIN <= len(s) <= params.KEY_TERM_CHARS_MAX):
                continue
            k = s.casefold()
            if k in seen:
                continue
            seen.add(k)
            out.append(s)
            if len(out) >= params.KEY_TERMS_MAX:
                break
        if len(out) < params.KEY_TERMS_MIN:
            raise ValueError(f"only {len(out)} usable unique key_terms (minimum {params.KEY_TERMS_MIN})")
        return out


class DocDistillateRaw(pydantic.BaseModel):
    """What page_distiller is asked to emit: the same fields as `DocDistillate` without the word and term-count validators.

    Near misses (a 64-word summary, two usable key terms) used to be rejected whole and replaced by a content-free
    fallback; the node's `normalize_distillate` clips and pads this into a valid `DocDistillate` instead."""

    model_config = pydantic.ConfigDict(extra = "forbid")

    summary: str = pydantic.Field(
        description = (
            f"{params.SUMMARY_WORDS_MIN}-{params.SUMMARY_WORDS_MAX} words. ONE sentence describing what THIS file "
            f"teaches or documents."
        ),
    )
    key_terms: list[str] = pydantic.Field(
        description = (
            f"{params.KEY_TERMS_MIN}-{params.KEY_TERMS_MAX} technical identifiers (function and class names, CLI "
            f"commands, config keys, type names) that appear in this doc."
        ),
    )
    role: DocRole = pydantic.Field(
        description = (
            "What kind of page this is. overview = landing, index or table-of-contents page that mainly points at other "
            "pages; cross_cutting = platform support, debugging, performance, threading or compatibility notes that "
            "apply across the whole framework rather than to one topic; tutorial = guided lesson; how_to = task "
            "recipe; reference = API or option listing; explanation = concepts and design rationale."
        ),
    )


# ----------------------------------------------------------------------------- chapter_architect, section_analyst

class ChapterProposal(pydantic.BaseModel):
    """One candidate chapter."""

    # Strict json_schema providers need additionalProperties:false on every object, nested ones included.
    model_config = pydantic.ConfigDict(extra = "forbid")

    title: str = pydantic.Field(
        description = (
            f"{params.TITLE_MIN_WORDS}-{params.TITLE_MAX_WORDS} words. Concrete noun phrase. Avoid generic "
            f"'Introduction', 'Overview', 'Conclusion': name the specific topic."
        ),
    )
    description: str = pydantic.Field(
        description = (
            f"{params.DESCRIPTION_CHARS_MIN}-{params.DESCRIPTION_CHARS_MAX} chars. One sentence describing what "
            f"readers learn in this chapter."
        ),
    )
    key_concepts: list[str] = pydantic.Field(
        description = (
            f"{params.CONCEPTS_MIN}-{params.CONCEPTS_MAX} technical concepts, identifiers or commands that belong in "
            f"this chapter. Specific names, not abstract topics."
        ),
    )

    @pydantic.field_validator("title")
    @classmethod
    def _validate_title(cls, v: str) -> str:
        s = " ".join(v.strip().split())
        n = len(s.split())
        if not (params.TITLE_MIN_WORDS <= n <= params.TITLE_MAX_WORDS):
            raise ValueError(f"title must be {params.TITLE_MIN_WORDS}-{params.TITLE_MAX_WORDS} words; got {n}")
        if s.casefold() in params.GENERIC_TITLES:
            raise ValueError(f"title {s!r} is a generic content-type name")
        return s

    @pydantic.field_validator("description")
    @classmethod
    def _validate_description(cls, v: str) -> str:
        s = " ".join(v.strip().split())
        if not (params.DESCRIPTION_CHARS_MIN <= len(s) <= params.DESCRIPTION_CHARS_MAX):
            raise ValueError(
                f"description must be {params.DESCRIPTION_CHARS_MIN}-{params.DESCRIPTION_CHARS_MAX} chars; got {len(s)}",
            )
        return s

    @pydantic.field_validator("key_concepts")
    @classmethod
    def _validate_concepts(cls, v: list[str]) -> list[str]:
        # Repair, don't reject: clip to CONCEPTS_MAX, drop out-of-range and duplicate concepts; only a real shortfall raises.
        out: list[str] = []
        seen: set[str] = set()
        for c in v:
            s = " ".join(c.strip().split())
            if not (params.CONCEPT_CHARS_MIN <= len(s) <= params.CONCEPT_CHARS_MAX):
                continue
            k = s.casefold()
            if k in seen:
                continue
            seen.add(k)
            out.append(s)
            if len(out) >= params.CONCEPTS_MAX:
                break
        if len(out) < params.CONCEPTS_MIN_ACCEPT:
            raise ValueError(f"only {len(out)} usable key_concepts (minimum {params.CONCEPTS_MIN_ACCEPT})")
        return out


def _keep_valid_proposals(value, limit: int):
    """Keep the valid proposals of a partly-bad answer (invalid items and case-insensitive duplicate titles are dropped,
    overflow clipped to `limit`) instead of rejecting the whole answer."""
    if not isinstance(value, list):
        return value
    out: list = []
    seen: set[str] = set()
    for item in value:
        try:
            p = item if isinstance(item, ChapterProposal) else ChapterProposal.model_validate(item)
        except Exception as e:
            # Never silent: a dropped chapter is a topic the corpus loses.
            t = item.get("title") if isinstance(item, dict) else item
            logger.info(f"[planner] dropped proposal {t!r}: {' '.join(str(e).split())[:140]}")
            continue
        k = p.title.casefold()
        if k in seen:
            logger.info(f"[planner] dropped duplicate proposal title {p.title!r}")
            continue
        seen.add(k)
        out.append(p)
        if len(out) >= limit:
            break
    return out


class ChapterProposalList(pydantic.BaseModel):
    """The whole corpus's chapter proposals (chapter_architect)."""

    model_config = pydantic.ConfigDict(extra = "forbid")

    proposals: list[ChapterProposal] = pydantic.Field(
        description = (
            f"{params.PROPOSALS_MIN}-{params.PROPOSALS_MAX} chapter proposals covering the full corpus surface area. "
            f"Each chapter is a distinct topic. Aim for balance: every chapter should be backed by at least 3 source docs."
        ),
    )

    @pydantic.field_validator("proposals", mode = "before")
    @classmethod
    def _drop_bad_proposals(cls, v):
        return _keep_valid_proposals(v, params.PROPOSALS_MAX)

    @pydantic.field_validator("proposals")
    @classmethod
    def _validate_count(cls, v: list[ChapterProposal]) -> list[ChapterProposal]:
        if len(v) < params.PROPOSALS_MIN:
            raise ValueError(f"only {len(v)} valid proposals (minimum {params.PROPOSALS_MIN})")
        return v


class SectionProposals(pydantic.BaseModel):
    """The chapters ONE section of the documentation supports (section_analyst); the architect merges the sections."""

    model_config = pydantic.ConfigDict(extra = "forbid")

    proposals: list[ChapterProposal] = pydantic.Field(
        description = (
            f"1-{params.SECTION_PROPOSALS_MAX} chapter proposals for the pages of this section only. Each chapter is a "
            f"distinct topic backed by at least 3 pages of the section; a section of fewer pages gets one chapter."
        ),
    )

    @pydantic.field_validator("proposals", mode = "before")
    @classmethod
    def _drop_bad_proposals(cls, v):
        return _keep_valid_proposals(v, params.SECTION_PROPOSALS_MAX)

    @pydantic.field_validator("proposals")
    @classmethod
    def _validate_count(cls, v: list[ChapterProposal]) -> list[ChapterProposal]:
        if not v:
            raise ValueError("no valid proposal for this section")
        return v


# ----------------------------------------------------------------------------- page_scorer

class ChapterScore(pydantic.BaseModel):
    """One score for one chapter."""

    model_config = pydantic.ConfigDict(extra = "forbid")

    chapter_idx: int = pydantic.Field(description = "Index into the proposals list.")
    confidence: float = pydantic.Field(
        description = (
            "0.0-1.0 confidence that this doc belongs to this chapter. Set 0.0 for chapters with no relevance; set 0.5+ "
            "only when the doc materially supports the chapter."
        ),
    )

    @pydantic.field_validator("confidence")
    @classmethod
    def _clamp(cls, v: float) -> float:
        if v < 0.0:
            return 0.0
        if v > 1.0:
            return 1.0
        return float(v)


class DocAssignment(pydantic.BaseModel):
    """The confidence of ONE doc against each chapter."""

    model_config = pydantic.ConfigDict(extra = "forbid")

    scores: list[ChapterScore] = pydantic.Field(
        description = "ONE score entry per chapter proposal (in the same order as the chapters list shown).",
    )


@functools.lru_cache(maxsize = 32)
def fixed_key_model(n: int) -> type[pydantic.BaseModel]:
    """ONE required float per chapter (`c0` .. `c{n-1}`) instead of a list of {chapter_idx, confidence}.

    On a 28-chapter plan the list came back cut short for 70% of pages while this form was complete for 100% of them, 36%
    faster per call: a missing key is a validation error the model cannot silently commit."""
    fields = {f"c{i}": (float, pydantic.Field(description = f"confidence 0.0-1.0 for chapter {i}")) for i in range(n)}
    return pydantic.create_model("FixedKeyAssignment", __config__ = pydantic.ConfigDict(extra = "forbid"), **fields)


def scores_from_fixed(obj: pydantic.BaseModel, n: int) -> DocAssignment:
    """The fixed-key answer as the usual DocAssignment (clamped by ChapterScore)."""
    return DocAssignment(scores = [ChapterScore(chapter_idx = i, confidence = float(getattr(obj, f"c{i}"))) for i in range(n)])


# ----------------------------------------------------------------------------- chapter_curator

class SplitGroup(pydantic.BaseModel):
    """One chapter carved out of an oversize chapter."""

    model_config = pydantic.ConfigDict(extra = "forbid")

    title: str = pydantic.Field(description = "Concrete 1-8 word chapter title, unique among all chapters.")
    description: str = pydantic.Field(description = "ONE sentence: what the reader learns in this chapter.")
    unit_ids: list[int] = pydantic.Field(description = "Ids of the units (from the list shown) that belong to this chapter.")


class SplitPlan(pydantic.BaseModel):
    """The oversize chapter partitioned into cohesive chapters."""

    model_config = pydantic.ConfigDict(extra = "forbid")

    groups: list[SplitGroup] = pydantic.Field(description = "The new chapters. Every unit id appears in exactly one group.")


# ----------------------------------------------------------------------------- chapter_sequencer

class ChapterTitleOrder(pydantic.BaseModel):
    """The chapter TITLES in reading order.

    Titles, not indices: a model that has to map a position number back to a chapter picks whole runs of the wrong chapters
    ("Synchronization Primitives" and "Queues" ahead of "Coroutines and Tasks"); the rationale comes first so the order
    follows from it."""

    model_config = pydantic.ConfigDict(extra = "forbid")

    rationale: str = pydantic.Field(description = "2-3 sentences: what a newcomer must read first and why.")
    order: list[str] = pydantic.Field(description = "Every chapter title exactly as given, in reading order.")
