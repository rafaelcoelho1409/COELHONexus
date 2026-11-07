"""The tool the chapter reviewer reads a section with before it decides to rewrite it."""
from __future__ import annotations
from . import params, schemas

from collections.abc import Callable

from langchain_core.tools import StructuredTool


def inspect_section_tool(read: Callable[[str], str | None]) -> StructuredTool:
    """`inspect_section(section_id)` over `read` (a section id → the section's text and issues, or None when there is no such section)."""

    async def inspect_section(section_id: str) -> str:
        text = read(section_id.strip())
        if text is None:
            return f"No section {section_id!r} in this chapter."
        return text[:params.SECTION_CHARS]

    return StructuredTool.from_function(
        coroutine = inspect_section,
        name = "inspect_section",
        description = "Returns one section of the failed chapter as rendered text (intro, subtopics with their code, citations) and its validator issues.",
        args_schema = schemas.SectionRef,
    )
