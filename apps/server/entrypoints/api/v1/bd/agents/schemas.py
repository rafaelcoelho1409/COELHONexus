"""HTTP request contracts for BD Ask."""
from __future__ import annotations

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    question: str = Field(min_length = 1, max_length = 8000)
    thread_id: str = Field(min_length = 1, max_length = 128, pattern = r"^[A-Za-z0-9_-]+$")
    book_id: str = Field(min_length = 1, max_length = 128, pattern = r"^[A-Za-z0-9_-]+$")
    force_mode: str | None = None
    sub_questions: list[str] = Field(default_factory = list, max_length = 4)


class BranchRequest(BaseModel):
    up_to_created_at: str | None = None
