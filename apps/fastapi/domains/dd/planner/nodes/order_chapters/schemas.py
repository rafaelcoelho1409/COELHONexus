"""order_chapters — Pydantic schema for the LLM ordering response."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ChapterTitleOrder(BaseModel):
    """LLM output: the chapter TITLES in reading order. Titles, not indices — a model that has to
    map a position number back to a chapter picks whole runs of the wrong chapters (asyncio:
    "Synchronization Primitives" and "Queues" ahead of "Coroutines and Tasks"); the rationale
    comes first so the order follows from it."""
    model_config = ConfigDict(extra = "forbid")

    rationale: str = Field(description = "2-3 sentences: what a newcomer must read first and why.")
    order: list[str] = Field(description = "Every chapter title exactly as given, in reading order.")
