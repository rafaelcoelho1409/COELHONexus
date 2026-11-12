"""What the model sends `check_patch`."""
from __future__ import annotations

import pydantic


class PatchInput(pydantic.BaseModel):
    issue_id: str = pydantic.Field(description = "The audited finding this patch resolves.")
    evidence: str = pydantic.Field(description = "That finding's evidence, copied exactly.")
    target_text: str = pydantic.Field(description = "The exact page text to replace; it must occur exactly once on the page.")
    replacement: str = pydantic.Field(description = "What replaces target_text.")
    confidence: float = pydantic.Field(description = "Your confidence that the patch is correct, 0 to 1.")
    context_before: str = pydantic.Field(default = "", description = "The exact page text immediately before target_text.")
    context_after: str = pydantic.Field(default = "", description = "The exact page text immediately after target_text.")
