from typing import Literal

from pydantic import BaseModel, Field


class PatchCheck(BaseModel):
    issue_id: str
    risk: Literal["low", "medium", "high", "critical"]
    target_text: str
    replacement: str
    confidence: float = Field(ge = 0, le = 1)
