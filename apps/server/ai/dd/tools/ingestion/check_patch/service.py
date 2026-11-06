"""The tool a page repairer checks one patch with before it answers.

The repairer proposes, deterministic code decides: the tool runs the same rules the application applies to the final answer (`check`,
built by the node over the page) and tells the model why a patch would be refused, so it can fix the patch in the same turn instead of
losing it. The page and its audit are read from the agent's state (`params.PAGE_PATH`, `params.ASSESSMENT_PATH`), so one agent serves
every page of a run."""
from __future__ import annotations
from . import params

import hashlib
import json
from collections.abc import Callable
from typing import Any

from langchain.tools import ToolRuntime
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field


# check(markdown, source_sha256, assessment, fields) -> "" when the patch would be applied, else why not (with what to change).
Check = Callable[[str, str, dict[str, Any], dict[str, Any]], str]


class PatchInput(BaseModel):
    issue_id: str = Field(description = "The audited finding this patch resolves.")
    evidence: str = Field(description = "That finding's evidence, copied exactly.")
    target_text: str = Field(description = "The exact page text to replace; it must occur exactly once on the page.")
    replacement: str = Field(description = "What replaces target_text.")
    confidence: float = Field(description = "Your confidence that the patch is correct, 0 to 1.")
    context_before: str = Field(default = "", description = "The exact page text immediately before target_text.")
    context_after: str = Field(default = "", description = "The exact page text immediately after target_text.")


def _file_text(files: dict[str, Any], path: str) -> str:
    content = (files.get(path) or {}).get("content", "")
    return content if isinstance(content, str) else "\n".join(content)


def tool(check: Check) -> StructuredTool:
    """`check_patch`, bound to the node's `check`."""
    def run(
        issue_id: str,
        evidence: str,
        target_text: str,
        replacement: str,
        confidence: float,
        context_before: str = "",
        context_after: str = "",
        *,
        runtime: ToolRuntime,
    ) -> str:
        files = runtime.state.get("files") or {}
        markdown = _file_text(files, params.PAGE_PATH)
        if not markdown:
            return "ERROR: the page is not available to check against; answer with the patches you have."
        assessment = json.loads(_file_text(files, params.ASSESSMENT_PATH) or "{}")
        fields = {
            "issue_id": issue_id,
            "evidence": evidence,
            "target_text": target_text,
            "replacement": replacement,
            "confidence": confidence,
            "context_before": context_before,
            "context_after": context_after,
        }
        reason = check(markdown, hashlib.sha256(markdown.encode("utf-8")).hexdigest(), assessment, fields)
        if not reason:
            return "OK: this patch would be applied. Keep it in your answer unchanged."
        return f"REJECTED: {reason[:params.REASON_CHARS]}"

    return StructuredTool.from_function(
        func = run,
        name = "check_patch",
        description = (
            "Checks ONE patch against the page with the application's own rules, with no model, and says whether it would be applied "
            "or why it would be refused and what to change. Call it for each patch before you answer, and fix or drop a refused patch."
        ),
        args_schema = PatchInput,
    )
