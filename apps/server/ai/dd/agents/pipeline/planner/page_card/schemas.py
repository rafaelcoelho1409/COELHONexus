"""The page-level semantic representation Planner v2 clusters."""
from __future__ import annotations

import typing

import pydantic


class PageCard(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra = "forbid")

    keep: bool = pydantic.Field(description = "Keep genuine framework documentation, including API/reference pages.")
    reason: str = pydantic.Field(description = "Brief source-grounded reason to keep or drop the page.")
    kind: typing.Literal["concept", "how_to", "tutorial", "reference", "overview"]
    topic: str = pydantic.Field(description = "A specific topic in at most eight words.")
    summary: str = pydantic.Field(description = "One concise sentence describing what this page teaches or documents.")
    teaches: list[str] = pydantic.Field(description = "Up to three concrete concepts, tasks, APIs, or commands on this page.")
    code_role: typing.Literal["none", "illustrative", "runnable_example", "signature_only"]
    prerequisites: list[str] = pydantic.Field(description = "Up to three prerequisite topics, not page titles.")

    @pydantic.field_validator("reason", "topic", "summary", mode = "before")
    @classmethod
    def _clean_text(cls, value):
        text = " ".join(str(value or "").split())
        return text[:240] if text else ""

    @pydantic.field_validator("teaches", "prerequisites", mode = "before")
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
