"""What the model sends `check_patch`."""
from __future__ import annotations

from pydantic import BaseModel, Field


class PatchInput(BaseModel):
    issue_id: str = Field(description = "The audited finding this patch resolves.")
    evidence: str = Field(description = "That finding's evidence, copied exactly.")
    target_text: str = Field(description = "The exact page text to replace; it must occur exactly once on the page.")
    replacement: str = Field(description = "What replaces target_text.")
    confidence: float = Field(description = "Your confidence that the patch is correct, 0 to 1.")
    context_before: str = Field(default = "", description = "The exact page text immediately before target_text.")
    context_after: str = Field(default = "", description = "The exact page text immediately after target_text.")
