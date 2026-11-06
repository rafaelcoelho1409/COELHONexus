"""The tool the chapter architect and plan_critic check an outline with: the corpus's evidence score, as text."""
from __future__ import annotations
from . import params

from collections.abc import Callable, Sequence

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field


class OutlineChapter(BaseModel):
    """A chapter of the outline being scored; a draft may be incomplete, so nothing here is validated."""

    title: str = Field(description = "The chapter's title.")
    description: str = Field(default = "", description = "One sentence on what readers learn.")
    key_concepts: list[str] = Field(default_factory = list, description = "Specific concepts, identifiers or commands.")


class OutlineInput(BaseModel):
    chapters: list[OutlineChapter] = Field(description = "The whole outline, every chapter.")


def report(scored: dict, chapters: Sequence[dict], labels: Sequence[str], target: int) -> str:
    """The score of one outline as the text the model reads: the three numbers, then the pages that match no chapter and the chapter sizes."""
    lines = [
        f"score {scored['score']} | {scored['n']} chapters (target {target}) | largest chapter "
        f"{scored['largest_share_pct']}% of the pages | {len(scored['unmatched'])} pages match no chapter ({scored['unmatched_pct']}%)",
    ]
    if scored["unmatched"]:
        shown = [labels[i] for i in scored["unmatched"][:params.UNMATCHED_LISTED]]
        more = len(scored["unmatched"]) - len(shown)
        lines.append("pages matching no chapter: " + ", ".join(shown) + (f" (+{more} more)" if more > 0 else ""))
    sizes = ", ".join(f"{i + 1}. {chapter['title']}: {n}" for i, (chapter, n) in enumerate(zip(chapters, scored["chapter_sizes"])))
    lines.append("pages matched per chapter: " + sizes)
    return "\n".join(lines)


def score_outline_tool(
    score: Callable[[list[dict]], dict],
    labels: Sequence[str],
    target: int,
) -> StructuredTool:
    """`score_outline(chapters)` over `score` (an outline → `domain.score_outline` result). `labels[i]` names page i by its path in the corpus."""

    async def score_outline(chapters: list[OutlineChapter]) -> str:
        outline = [chapter.model_dump() for chapter in chapters]
        return report(score(outline), outline, labels, target)

    return StructuredTool.from_function(
        coroutine = score_outline,
        name = "score_outline",
        description = (
            "Scores a whole chapter outline against the corpus with no model: the share of pages that match no chapter, the "
            "largest chapter's share of the pages, the chapter count against the target, and which pages are unmatched "
            "(as /corpus paths you can read). Call it with every chapter after each change."
        ),
        args_schema = OutlineInput,
    )
