from langchain.tools import ToolRuntime, tool


@tool
def check_patch(
    issue_id: str, 
    target_text: str, 
    replacement: str, 
    runtime: ToolRuntime) -> str:
    """Check one patch against the page before you answer.

    Say what the application would do with it: reject it (and why), keep it for human review, or apply it automatically.
    `issue_id` is one of your assigned issues; `target_text` must be copied exactly from the supplied Markdown and occur
    once. Fix a rejected patch and check it again, or leave it out. This tool changes nothing.
    """
    return runtime.context.check_patch(issue_id, target_text, replacement)
