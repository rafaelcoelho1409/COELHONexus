"""ycs/grader — structured-output Pydantic for the relevance LLM call.

Direct port of deprecated `schemas/youtube/agents.py:L56-60` +
2026-06-16 ternary grade. The binary `relevant` / `not_relevant`
schema was empirically too strict for DEEP-mode abstract sub-
questions ("recurring emotional tones?", "evolution across video
categories?") — a transcript stating "I'm tired of Brazil" is highly
relevant to "emotional tones" but the LLM grader rejected it because
the literal phrase "emotional tone" was absent. Result: ~60% of
DEEP sub-questions came back with `error_kind="no_docs"` and the
final synthesis was thin.

The ternary middle class `likely_relevant` lets the LLM signal
partial / lateral relevance — the document touches the question
without explicitly addressing it. Downstream
`service.py::grade_documents` treats `relevant` AND `likely_relevant`
as keepers; only `not_relevant` is dropped. The string type (rather
than `Literal[...]`) is kept on purpose: free-tier models occasionally
emit slight variants (`RELEVANT`, `partially relevant`, `relevant.`)
and the `_rescue_score` fallback in `service.py` handles all of
those gracefully. A strict `Literal` would void the rescue path."""
from __future__ import annotations

from pydantic import BaseModel, Field


class GradeResult(BaseModel):
    """Ternary relevance grade for a document."""
    score: str = Field(
        description = (
            "'relevant' if the document directly answers the question, "
            "'likely_relevant' if it touches the topic / provides "
            "lateral or partial evidence, "
            "'not_relevant' if it has no useful information for "
            "the question"
        ),
    )
