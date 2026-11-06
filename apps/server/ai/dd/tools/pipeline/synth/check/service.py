"""The tools a synth agent checks its draft with: each runs the stage's own validators over the draft and returns the issues as text.

The agent proposes, deterministic code decides: the tools take the draft as the model wrote it (lenient inputs), and a `check` callable the
node built over the run's data (the outline's ids, a section's code bank, the source's hashes) reports what is wrong."""
from __future__ import annotations
from . import params

from collections.abc import Awaitable, Callable

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field


Check = Callable[[dict], Awaitable[list[str]]]


def report(issues: list[str]) -> str:
    """The text the model reads: a single line when the draft is valid, else every issue (the first ones, if there are many)."""
    if not issues:
        return "OK: no issues. The draft is valid; answer now."
    shown = issues[:params.ISSUES_SHOWN]
    more = len(issues) - len(shown)
    lines = [f"{len(issues)} issue(s) to fix:"] + [f"- {issue[:params.ISSUE_CHARS]}" for issue in shown]
    if more > 0:
        lines.append(f"- ... and {more} more")
    return "\n".join(lines)


def tool(name: str, description: str, args_schema: type[BaseModel], check: Check) -> StructuredTool:
    async def run(**fields) -> str:
        return report(await check(args_schema(**fields).model_dump()))

    return StructuredTool.from_function(coroutine = run, name = name, description = description, args_schema = args_schema)


class OutlineSectionInput(BaseModel):
    section_id: str = ""
    heading: str = ""
    description: str = ""
    prerequisites: list[str] = Field(default_factory = list)
    needs_code: bool = True


class OutlineInput(BaseModel):
    sections: list[OutlineSectionInput] = Field(description = "The whole outline, every section.")


def validate_outline_tool(check: Check) -> StructuredTool:
    return tool(
        "validate_outline",
        "Checks a whole chapter outline with no model: section ids, heading and description rules, prerequisites (cycles, forward "
        "references, depth), the section floor and cap for this chapter, and sections whose scope overlaps. Call it with every section "
        "after each change.",
        OutlineInput,
        check,
    )


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


def check_digest_tool(check: Check) -> StructuredTool:
    return tool(
        "check_digest",
        "Checks a source digest with no model: every section_id is in the outline, every code ref is a hash of this source, no hash is "
        "routed twice. Call it with the whole digest after each change.",
        DigestInput,
        check,
    )


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


def check_section_tool(check: Check) -> StructuredTool:
    return tool(
        "check_section",
        "Checks a whole section draft with the validator that judges the final answer, no model: the heading echo, code hashes that are "
        "in the bank and not repeated, citations that are valid source keys, explanations that name an identifier of their block, "
        "subheadings grounded in their block. Hard issues must be fixed; soft ones may stay. Call it with the whole section after each change.",
        SectionInput,
        check,
    )


class CodeInput(BaseModel):
    code: str = Field(default = "", description = "The code only, no fences.")


def check_code_tool(check: Check) -> StructuredTool:
    return tool(
        "check_code",
        "Checks code with no model: that it parses, and for a derived example that its body is inside the length band. Call it with the "
        "whole code after each change.",
        CodeInput,
        check,
    )
