"""The tools a synth agent checks its draft with: each runs the stage's own validators over the draft and returns the issues as text.

The agent proposes, deterministic code decides: the tools take the draft as the model wrote it (lenient inputs), and a `check` callable the
node built over the run's data (the outline's ids, a section's code bank, the source's hashes) reports what is wrong."""
from __future__ import annotations
from . import entities, schemas, service

from langchain_core.tools import StructuredTool
from pydantic import BaseModel


def check_tool(name: str, description: str, args_schema: type[BaseModel], check: entities.Check) -> StructuredTool:
    async def run(**fields) -> str:
        return service.report(await check(args_schema(**fields).model_dump()))

    return StructuredTool.from_function(coroutine = run, name = name, description = description, args_schema = args_schema)


def validate_outline_tool(check: entities.Check) -> StructuredTool:
    return check_tool(
        "validate_outline",
        "Checks a whole chapter outline with no model: section ids, heading and description rules, prerequisites (cycles, forward "
        "references, depth), the section floor and cap for this chapter, and sections whose scope overlaps. Call it with every section "
        "after each change.",
        schemas.OutlineInput,
        check,
    )


def check_digest_tool(check: entities.Check) -> StructuredTool:
    return check_tool(
        "check_digest",
        "Checks a source digest with no model: every section_id is in the outline, every code ref is a hash of this source, no hash is "
        "routed twice. Call it with the whole digest after each change.",
        schemas.DigestInput,
        check,
    )


def check_section_tool(check: entities.Check) -> StructuredTool:
    return check_tool(
        "check_section",
        "Checks a whole section draft with the validator that judges the final answer, no model: the heading echo, code hashes that are "
        "in the bank and not repeated, citations that are valid source keys, explanations that name an identifier of their block, "
        "subheadings grounded in their block. Hard issues must be fixed; soft ones may stay. Call it with the whole section after each change.",
        schemas.SectionInput,
        check,
    )


def check_code_tool(check: entities.Check) -> StructuredTool:
    return check_tool(
        "check_code",
        "Checks code with no model: that it parses, and for a derived example that its body is inside the length band. Call it with the "
        "whole code after each change.",
        schemas.CodeInput,
        check,
    )
