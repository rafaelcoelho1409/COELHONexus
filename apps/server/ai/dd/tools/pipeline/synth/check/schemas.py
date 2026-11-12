"""What the model sends the synth check tools: its draft, as it wrote it, so every field is lenient."""
from __future__ import annotations

import pydantic


class OutlineSectionInput(pydantic.BaseModel):
    section_id: str = ""
    heading: str = ""
    description: str = ""
    prerequisites: list[str] = pydantic.Field(default_factory = list)
    needs_code: bool = True


class OutlineInput(pydantic.BaseModel):
    sections: list[OutlineSectionInput] = pydantic.Field(description = "The whole outline, every section.")


class ContributionInput(pydantic.BaseModel):
    section_id: str = ""
    relevance: str = ""
    summary: str = ""
    key_facts: list[str] = pydantic.Field(default_factory = list)
    code_refs: list[str] = pydantic.Field(default_factory = list)


class DigestInput(pydantic.BaseModel):
    source_title: str = ""
    overall_summary: str = ""
    contributes_to: list[ContributionInput] = pydantic.Field(default_factory = list)
    unassigned_code_refs: list[str] = pydantic.Field(default_factory = list)


class SubtopicInput(pydantic.BaseModel):
    code_ref_hash: str = ""
    subheading: str = ""
    explanation: str = ""


class CitationInput(pydantic.BaseModel):
    source_key: str = ""
    claim: str = ""


class SectionInput(pydantic.BaseModel):
    heading: str = ""
    intro: str = ""
    subtopics: list[SubtopicInput] = pydantic.Field(default_factory = list)
    citations: list[CitationInput] = pydantic.Field(default_factory = list)


class CodeInput(pydantic.BaseModel):
    code: str = pydantic.Field(default = "", description = "The code only, no fences.")
