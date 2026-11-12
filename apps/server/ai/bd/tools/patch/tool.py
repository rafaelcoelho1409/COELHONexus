from langchain.tools import tool, ToolRuntime
from pydantic import BaseModel


class PatchCheck(BaseModel):
    issue_id: str
    target_text: str
    replacement: str


@tool
def check_patches(
    patches: list[PatchCheck],
    runtime: ToolRuntime) -> str:
    """Check all the patches you plan to send, in one call, before you answer.

    For each patch it says what the application would do with it: reject it (and why), keep it for human review, or apply
    it automatically. `issue_id` is one of your assigned issues; `target_text` must be copied exactly from the supplied
    Markdown and occur once. Fix the rejected patches and check them again together, or leave them out. This tool changes
    nothing.
    """
    return "\n".join(
        f"{number}. {patch.issue_id}: {runtime.context.check_patch(patch.issue_id, patch.target_text, patch.replacement)}"
        for number, patch in enumerate(patches, start = 1)
    ) or "no patches were given"
