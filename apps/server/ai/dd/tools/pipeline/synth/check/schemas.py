"""What the model sends the synth check tools: its draft, as it wrote it, so every field is lenient."""
from __future__ import annotations

from pydantic import BaseModel, Field


class OutlineSectionInput(BaseModel):
    section_id: str = ""
    heading: str = ""
    description: str = ""
    prerequisites: list[str] = Field(default_factory = list)
    needs_code: bool = True


class OutlineInput(BaseModel):
    sections: list[OutlineSectionInput] = Field(description = "The whole outline, every section.")


class ContributionInput(BaseModel):
    section_id: str = ""
    relevance: str = ""
    summary: str = ""
    key_facts: list[str] = Field(default_factory = list)
    code_refs: list[str] = Field(default_factory = list)


class DigestInput(BaseModel):
    source_title: str = ""
    overall_summary: str = ""
    contributes_to: list[ContributionInput] = Field(default_factory = list)
    unassigned_code_refs: list[str] = Field(default_factory = list)


class SubtopicInput(BaseModel):
    code_ref_hash: str = ""
    subheading: str = ""
    explanation: str = ""


class CitationInput(BaseModel):
    source_key: str = ""
    claim: str = ""


class SectionInput(BaseModel):
    heading: str = ""
    intro: str = ""
    subtopics: list[SubtopicInput] = Field(default_factory = list)
    citations: list[CitationInput] = Field(default_factory = list)


class CodeInput(BaseModel):
    code: str = Field(default = "", description = "The code only, no fences.")
