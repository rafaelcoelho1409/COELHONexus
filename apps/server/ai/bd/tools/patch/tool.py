from typing import Literal

from langchain.tools import ToolRuntime, tool
from pydantic import BaseModel, Field


class PatchCheck(BaseModel):
    issue_id: str
    risk: Literal["low", "medium", "high", "critical"]
    target_text: str
    replacement: str
    confidence: float = Field(ge = 0, le = 1)


@tool
def check_patches(patches: list[PatchCheck], runtime: ToolRuntime) -> str:
    """Check all the patches you plan to send, in one call, before you answer.

    For each patch it says whether the application rejects it, sends it for source-image verification, or preserves and
    reports it. Only a deterministic safe fix or an exact source-image-confirmed correction is applied automatically.
    `issue_id` is one of your assigned issues; `target_text` must be copied exactly from the supplied Markdown.
    Include the patch's actual risk and confidence so this check matches the final safety gate. This tool changes nothing.
    """
    results = []
    for number, patch in enumerate(patches, start = 1):
        outcome = runtime.context.check_patch(
            patch.issue_id,
            patch.risk,
            patch.target_text,
            patch.replacement,
            patch.confidence,
        )
        results.append(f"{number}. {patch.issue_id}: {outcome}")
    return "\n".join(results) or "no patches were given"
