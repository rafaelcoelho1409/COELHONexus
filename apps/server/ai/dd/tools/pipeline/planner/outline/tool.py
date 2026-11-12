"""The tool the chapter architect and plan_critic check an outline with: the corpus's evidence score, as text."""
from __future__ import annotations
from . import schemas, service

from collections.abc import Callable, Sequence

from langchain_core.tools import StructuredTool


def score_outline_tool(
    score: Callable[[list[dict]], dict],
    labels: Sequence[str],
    target: int,
) -> StructuredTool:
    """`score_outline(chapters)` over `score` (an outline → `domain.score_outline` result). `labels[i]` names page i by its path in the corpus."""

    async def score_outline(chapters: list[schemas.OutlineChapter]) -> str:
        outline = [chapter.model_dump() for chapter in chapters]
        return service.report(score(outline), outline, labels, target)

    return StructuredTool.from_function(
        coroutine = score_outline,
        name = "score_outline",
        description = (
            "Scores a whole chapter outline against the corpus with no model: the share of pages that match no chapter, the "
            "largest chapter's share of the pages, the chapter count against the target, and which pages are unmatched "
            "(as /corpus paths you can read). Call it with every chapter after each change."
        ),
        args_schema = schemas.OutlineInput,
    )
