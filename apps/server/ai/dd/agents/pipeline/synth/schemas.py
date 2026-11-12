"""What the synth agents answer. The models are deliberately lenient (no length or count limits): the answer is the model's, and the
node that receives it trims it and validates it with the stage's own rules (`nodes.pipeline.synth.<stage>.domain`), so a draft one word
over a bound is trimmed instead of rejected. The bounds are written into each field's description for the model."""
from __future__ import annotations

from pydantic import BaseModel, Field


class OutlineSectionAnswer(BaseModel):
    section_id: str = Field(description = "'s' + an integer: s1, s2, ... unique within the chapter; never a descriptive slug.")
    heading: str = Field(description = "2-8 words, topic-specific, no leading '#'.")
    description: str = Field(description = "20-400 chars, one specific topic; used to route source material to this section.")
    prerequisites: list[str] = Field(default_factory = list, description = "0-3 ids of EARLIER sections this one structurally depends on.")
    needs_code: bool = Field(default = True, description = "False for pure design or conceptual narrative.")
    code_refs: list[str] = Field(default_factory = list, description = "In tutorial mode, the 16-hex canonical code-ledger hashes this step teaches; use at most one best example per step.")


class OutlineAnswer(BaseModel):
    """The chapter's sections (about the target count; hard range 2-40) with their prerequisites."""

    sections: list[OutlineSectionAnswer]


class ContributionAnswer(BaseModel):
    section_id: str = Field(description = "One of the outline's section ids.")
    relevance: str = Field(description = "'primary', 'supporting' or 'tangential'.")
    summary: str = Field(description = "1-3 sentences (20-600 chars) naming the concrete APIs this source gives the section.")
    key_facts: list[str] = Field(description = "1-5 standalone claims (6-300 chars each).")
    code_refs: list[str] = Field(default_factory = list, description = "16-hex hashes from this source that belong to this section.")


class DigestAnswer(BaseModel):
    """Where one source page contributes in the chapter's outline."""

    source_title: str = Field(description = "3-200 chars.")
    overall_summary: str = Field(description = "One paragraph (30-800 chars) on what the source is about.")
    contributes_to: list[ContributionAnswer] = Field(description = "One entry per outline section the source really contributes to (never empty).")
    unassigned_code_refs: list[str] = Field(default_factory = list, description = "Hashes in the source routed to no section.")


class SubtopicAnswer(BaseModel):
    code_ref_hash: str = Field(default = "", description = "16-hex hash from the section's code bank; empty for a prose subtopic.")
    subheading: str = Field(description = "2-10 words naming what the chosen block demonstrates.")
    explanation: str = Field(description = "8-80 words (40-80 for a prose subtopic) that name an identifier visible in the block. No code fences.")


class CitationAnswer(BaseModel):
    source_key: str = Field(description = "One of the section's valid source keys.")
    claim: str = Field(description = "6-400 chars: the fact this source backs.")


class SectionAnswer(BaseModel):
    """One section of a chapter: a cookbook of (subheading, explanation, code block) triples."""

    heading: str = Field(description = "The outline heading, verbatim.")
    intro: str = Field(description = "1-2 sentences (20-400 chars) of substance, opening with the subject. No code fences.")
    subtopics: list[SubtopicAnswer] = Field(description = "3-12 subtopics, easiest first.")
    citations: list[CitationAnswer] = Field(default_factory = list, description = "Up to 12 citations of the section's valid source keys.")


class ExtensionAnswer(BaseModel):
    """Extra subtopics for a section that is already written."""

    subtopics: list[SubtopicAnswer] = Field(default_factory = list)
    citations: list[CitationAnswer] = Field(default_factory = list)


class SourceSubtopicAnswer(BaseModel):
    subheading: str = Field(description = "2-10 words naming the topic.")
    explanation: str = Field(description = "18-80 words of connected sentences stating facts taken only from the listed facts.")
    sources: list[str] = Field(default_factory = list, description = "The page keys, exactly as listed, the subtopic used.")


class SourceExtensionAnswer(BaseModel):
    """Prose subtopics for sources the section never covered."""

    subtopics: list[SourceSubtopicAnswer] = Field(default_factory = list)
    citations: list[CitationAnswer] = Field(default_factory = list)


class DerivedCodeAnswer(BaseModel):
    """A complete runnable example for a thin documentation reference, and the explanation that goes with it."""

    code: str = Field(description = "The example's source code only: no fences, no prose.")
    explanation: str = Field(default = "", description = "8-80 words (1-3 sentences) describing this code and naming an identifier in it. No fences.")


class CapstonePartAnswer(BaseModel):
    subheading: str = Field(description = "1-10 words naming what this part of the application builds; no leading '#'.")
    explanation: str = Field(description = "8-80 words that say what this part does and which step of the chapter it uses, naming an identifier in its code. No fences.")
    code: str = Field(description = "This part's source code only: no fences, no prose. Python that parses.")


class CapstoneAnswer(BaseModel):
    """The application that ends a chapter: it combines the best code the chapter teaches, in one to four parts that together run."""

    title: str = Field(description = "2-8 words naming the application, without the word 'Capstone'.")
    intro: str = Field(description = "20-400 chars: what the application does and which steps of the chapter it combines. No fences.")
    parts: list[CapstonePartAnswer] = Field(description = "1-4 parts in the order a reader builds them; the last says how to run the whole.")


class NormalizedCodeAnswer(BaseModel):
    """A code block with its whitespace repaired."""

    code: str = Field(description = "The repaired code only: no fences, no commentary. Only whitespace may differ from the input.")


class VerdictAnswer(BaseModel):
    passed: bool
    feedback: str = Field(default = "", description = "One specific sentence naming a section and the symptom when the criterion fails; empty when it passes.")


class JudgeAnswer(BaseModel):
    """The five binary criteria of the chapter checklist."""

    chapter_reads_coherently: VerdictAnswer
    claims_grounded_in_sources: VerdictAnswer
    terminology_consistent: VerdictAnswer
    prose_code_first_not_meta_framing: VerdictAnswer
    code_refs_introduced_in_prose: VerdictAnswer


class RevisionAnswer(BaseModel):
    """Which sections of a chapter that failed its checklist should be written again, and what to change."""

    halt: bool = Field(default = False, description = "True when no rewrite would help: the failures are not about particular sections or the chapter is good enough.")
    confidence: float = Field(default = 0.5, description = "0-1: how sure you are that the rewrites will clear the failures.")
    rationale: str = Field(default = "", description = "One or two sentences on the choice.")
    revisions: list["RevisionItem"] = Field(default_factory = list, description = "The sections to write again (at most 6).")


class RevisionItem(BaseModel):
    section_id: str = Field(description = "A section id of the chapter outline.")
    instruction: str = Field(description = "What to fix in that section, in one or two sentences, naming the failed criterion.")


RevisionAnswer.model_rebuild()


class TermAnswer(BaseModel):
    name: str = Field(description = "The term as the chapter uses it.")
    definition: str = Field(description = "One sentence, as used in this chapter.")


class ClaimsAnswer(BaseModel):
    """The atomic claims and key terms of one chapter."""

    claims: list[str] = Field(default_factory = list, description = "Single verifiable assertions about the technology (at most 20).")
    terms: list[TermAnswer] = Field(default_factory = list)


class CanonicalTermAnswer(BaseModel):
    term: str
    canonical_definition: str
    affected_chapters: list[str] = Field(default_factory = list)


class CanonicalAnswer(BaseModel):
    """The terms that chapters define differently, settled once."""

    canonical_terms: list[CanonicalTermAnswer] = Field(default_factory = list)
    rationale: str = ""


class ViolationAnswer(BaseModel):
    kind: str = Field(description = "'contradiction', 'definition_drift' or 'terminology_divergence'.")
    this_chapter_says: str = Field(description = "EXACT words copied from this chapter's prose.")
    should_say: str = Field(description = "The canonical or sibling-chapter version.")
    sibling_chapter: str = Field(default = "", description = "The chapter id in brackets in the sibling claims (empty for a canonical-term issue).")
    sibling_claim: str = Field(default = "", description = "The sibling claim, or canonical term, this conflicts with, copied from the lists.")


class ViolationsAnswer(BaseModel):
    """Cross-chapter consistency problems in one chapter."""

    has_violations: bool = False
    violations: list[ViolationAnswer] = Field(default_factory = list)
    summary: str = ""


class ParagraphAnswer(BaseModel):
    """One paragraph, minimally edited."""

    paragraph: str = Field(description = "The edited paragraph only: no quotes, no fences, no commentary.")
