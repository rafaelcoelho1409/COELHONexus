"""The page-level semantic representation Planner v2 clusters."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PageCard(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    keep: bool = Field(description = "Keep genuine framework documentation, including API/reference pages.")
    reason: str = Field(description = "Brief source-grounded reason to keep or drop the page.")
    kind: Literal["concept", "how_to", "tutorial", "reference", "overview"]
    topic: str = Field(description = "A specific topic in at most eight words.")
    summary: str = Field(description = "One concise sentence describing what this page teaches or documents.")
    teaches: list[str] = Field(description = "Up to three concrete concepts, tasks, APIs, or commands on this page.")
    code_role: Literal["none", "illustrative", "runnable_example", "signature_only"]
    prerequisites: list[str] = Field(description = "Up to three prerequisite topics, not page titles.")

    @field_validator("reason", "topic", "summary", mode = "before")
    @classmethod
    def _clean_text(cls, value):
        text = " ".join(str(value or "").split())
        return text[:240] if text else ""

    @field_validator("teaches", "prerequisites", mode = "before")
    @classmethod
    def _clean_list(cls, value):
        if not isinstance(value, list):
            return []
        out = []
        seen = set()
        for item in value:
            text = " ".join(str(item or "").split())
            if text and text.casefold() not in seen:
                seen.add(text.casefold())
                out.append(text[:160])
            if len(out) >= 3:
                break
        return out
