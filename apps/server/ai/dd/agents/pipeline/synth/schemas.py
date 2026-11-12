"""What the synth agents answer. The models are deliberately lenient (no length or count limits): the answer is the model's, and the
node that receives it trims it and validates it with the stage's own rules (`nodes.pipeline.synth.<stage>.domain`), so a draft one word
over a bound is trimmed instead of rejected. The bounds are written into each field's description for the model."""
from __future__ import annotations

import pydantic


class OutlineSectionAnswer(pydantic.BaseModel):
    section_id: str = pydantic.Field(description = "'s' + an integer: s1, s2, ... unique within the chapter; never a descriptive slug.")
    heading: str = pydantic.Field(description = "2-8 words, topic-specific, no leading '#'.")
    description: str = pydantic.Field(description = "20-400 chars, one specific topic; used to route source material to this section.")
    prerequisites: list[str] = pydantic.Field(default_factory = list, description = "0-3 ids of EARLIER sections this one structurally depends on.")
    needs_code: bool = pydantic.Field(default = True, description = "False for pure design or conceptual narrative.")
    code_refs: list[str] = pydantic.Field(default_factory = list, description = "In tutorial mode, the 16-hex canonical code-ledger hashes this step teaches; use at most one best example per step.")


class OutlineAnswer(pydantic.BaseModel):
    """The chapter's sections (about the target count; hard range 2-40) with their prerequisites."""

    sections: list[OutlineSectionAnswer]


class ContributionAnswer(pydantic.BaseModel):
    section_id: str = pydantic.Field(description = "One of the outline's section ids.")
    relevance: str = pydantic.Field(description = "'primary', 'supporting' or 'tangential'.")
    summary: str = pydantic.Field(description = "1-3 sentences (20-600 chars) naming the concrete APIs this source gives the section.")
    key_facts: list[str] = pydantic.Field(description = "1-5 standalone claims (6-300 chars each).")
    code_refs: list[str] = pydantic.Field(default_factory = list, description = "16-hex hashes from this source that belong to this section.")


class DigestAnswer(pydantic.BaseModel):
    """Where one source page contributes in the chapter's outline."""

    source_title: str = pydantic.Field(description = "3-200 chars.")
    overall_summary: str = pydantic.Field(description = "One paragraph (30-800 chars) on what the source is about.")
    contributes_to: list[ContributionAnswer] = pydantic.Field(description = "One entry per outline section the source really contributes to (never empty).")
    unassigned_code_refs: list[str] = pydantic.Field(default_factory = list, description = "Hashes in the source routed to no section.")


class SubtopicAnswer(pydantic.BaseModel):
    code_ref_hash: str = pydantic.Field(default = "", description = "16-hex hash from the section's code bank; empty for a prose subtopic.")
    subheading: str = pydantic.Field(description = "2-10 words naming what the chosen block demonstrates.")
    explanation: str = pydantic.Field(description = "8-80 words (40-80 for a prose subtopic) that name an identifier visible in the block. No code fences.")


class CitationAnswer(pydantic.BaseModel):
    source_key: str = pydantic.Field(description = "One of the section's valid source keys.")
    claim: str = pydantic.Field(description = "6-400 chars: the fact this source backs.")


class SectionAnswer(pydantic.BaseModel):
    """One section of a chapter: a cookbook of (subheading, explanation, code block) triples."""

    heading: str = pydantic.Field(description = "The outline heading, verbatim.")
    intro: str = pydantic.Field(description = "1-2 sentences (20-400 chars) of substance, opening with the subject. No code fences.")
    subtopics: list[SubtopicAnswer] = pydantic.Field(description = "3-12 subtopics, easiest first.")
    citations: list[CitationAnswer] = pydantic.Field(default_factory = list, description = "Up to 12 citations of the section's valid source keys.")


class ExtensionAnswer(pydantic.BaseModel):
    """Extra subtopics for a section that is already written."""

    subtopics: list[SubtopicAnswer] = pydantic.Field(default_factory = list)
    citations: list[CitationAnswer] = pydantic.Field(default_factory = list)


class SourceSubtopicAnswer(pydantic.BaseModel):
    subheading: str = pydantic.Field(description = "2-10 words naming the topic.")
    explanation: str = pydantic.Field(description = "18-80 words of connected sentences stating facts taken only from the listed facts.")
    sources: list[str] = pydantic.Field(default_factory = list, description = "The page keys, exactly as listed, the subtopic used.")


class SourceExtensionAnswer(pydantic.BaseModel):
    """Prose subtopics for sources the section never covered."""

    subtopics: list[SourceSubtopicAnswer] = pydantic.Field(default_factory = list)
    citations: list[CitationAnswer] = pydantic.Field(default_factory = list)


class DerivedCodeAnswer(pydantic.BaseModel):
    """A complete runnable example for a thin documentation reference, and the explanation that goes with it."""

    code: str = pydantic.Field(description = "The example's source code only: no fences, no prose.")
    explanation: str = pydantic.Field(default = "", description = "8-80 words (1-3 sentences) describing this code and naming an identifier in it. No fences.")


class CapstonePartAnswer(pydantic.BaseModel):
    subheading: str = pydantic.Field(description = "1-10 words naming what this part of the application builds; no leading '#'.")
    explanation: str = pydantic.Field(description = "8-80 words that say what this part does and which step of the chapter it uses, naming an identifier in its code. No fences.")
    code: str = pydantic.Field(description = "This part's source code only: no fences, no prose. Python that parses.")


class CapstoneAnswer(pydantic.BaseModel):
    """The application that ends a chapter: it combines the best code the chapter teaches, in one to four parts that together run."""

    title: str = pydantic.Field(description = "2-8 words naming the application, without the word 'Capstone'.")
    intro: str = pydantic.Field(description = "20-400 chars: what the application does and which steps of the chapter it combines. No fences.")
    parts: list[CapstonePartAnswer] = pydantic.Field(description = "1-4 parts in the order a reader builds them; the last says how to run the whole.")


class NormalizedCodeAnswer(pydantic.BaseModel):
    """A code block with its whitespace repaired."""

    code: str = pydantic.Field(description = "The repaired code only: no fences, no commentary. Only whitespace may differ from the input.")


class VerdictAnswer(pydantic.BaseModel):
    passed: bool
    feedback: str = pydantic.Field(default = "", description = "One specific sentence naming a section and the symptom when the criterion fails; empty when it passes.")


class JudgeAnswer(pydantic.BaseModel):
    """The five binary criteria of the chapter checklist."""

    chapter_reads_coherently: VerdictAnswer
    claims_grounded_in_sources: VerdictAnswer
    terminology_consistent: VerdictAnswer
    prose_code_first_not_meta_framing: VerdictAnswer
    code_refs_introduced_in_prose: VerdictAnswer


class RevisionAnswer(pydantic.BaseModel):
    """Which sections of a chapter that failed its checklist should be written again, and what to change."""

    halt: bool = pydantic.Field(default = False, description = "True when no rewrite would help: the failures are not about particular sections or the chapter is good enough.")
    confidence: float = pydantic.Field(default = 0.5, description = "0-1: how sure you are that the rewrites will clear the failures.")
    rationale: str = pydantic.Field(default = "", description = "One or two sentences on the choice.")
    revisions: list["RevisionItem"] = pydantic.Field(default_factory = list, description = "The sections to write again (at most 6).")


class RevisionItem(pydantic.BaseModel):
    section_id: str = pydantic.Field(description = "A section id of the chapter outline.")
    instruction: str = pydantic.Field(description = "What to fix in that section, in one or two sentences, naming the failed criterion.")


RevisionAnswer.model_rebuild()


class TermAnswer(pydantic.BaseModel):
    name: str = pydantic.Field(description = "The term as the chapter uses it.")
    definition: str = pydantic.Field(description = "One sentence, as used in this chapter.")


class ClaimsAnswer(pydantic.BaseModel):
    """The atomic claims and key terms of one chapter."""

    claims: list[str] = pydantic.Field(default_factory = list, description = "Single verifiable assertions about the technology (at most 20).")
    terms: list[TermAnswer] = pydantic.Field(default_factory = list)


class CanonicalTermAnswer(pydantic.BaseModel):
    term: str
    canonical_definition: str
    affected_chapters: list[str] = pydantic.Field(default_factory = list)


class CanonicalAnswer(pydantic.BaseModel):
    """The terms that chapters define differently, settled once."""

    canonical_terms: list[CanonicalTermAnswer] = pydantic.Field(default_factory = list)
    rationale: str = ""


class ViolationAnswer(pydantic.BaseModel):
    kind: str = pydantic.Field(description = "'contradiction', 'definition_drift' or 'terminology_divergence'.")
    this_chapter_says: str = pydantic.Field(description = "EXACT words copied from this chapter's prose.")
    should_say: str = pydantic.Field(description = "The canonical or sibling-chapter version.")
    sibling_chapter: str = pydantic.Field(default = "", description = "The chapter id in brackets in the sibling claims (empty for a canonical-term issue).")
    sibling_claim: str = pydantic.Field(default = "", description = "The sibling claim, or canonical term, this conflicts with, copied from the lists.")


class ViolationsAnswer(pydantic.BaseModel):
    """Cross-chapter consistency problems in one chapter."""

    has_violations: bool = False
    violations: list[ViolationAnswer] = pydantic.Field(default_factory = list)
    summary: str = ""


class ParagraphAnswer(pydantic.BaseModel):
    """One paragraph, minimally edited."""

    paragraph: str = pydantic.Field(description = "The edited paragraph only: no quotes, no fences, no commentary.")
