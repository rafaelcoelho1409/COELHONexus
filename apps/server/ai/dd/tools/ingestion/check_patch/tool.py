"""The tool a page repairer checks one patch with before it answers.

The repairer proposes, deterministic code decides: the tool runs the same rules the application applies to the final answer (`check`,
built by the node over the page) and tells the model why a patch would be refused, so it can fix the patch in the same turn instead of
losing it. The page and its audit are read from the agent's state (`params.PAGE_PATH`, `params.ASSESSMENT_PATH`), so one agent serves
every page of a run."""
from __future__ import annotations
from . import entities, schemas, service

from langchain.tools import ToolRuntime
from langchain_core.tools import StructuredTool


def check_patch_tool(check: entities.Check) -> StructuredTool:
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
        fields = {
            "issue_id": issue_id,
            "evidence": evidence,
            "target_text": target_text,
            "replacement": replacement,
            "confidence": confidence,
            "context_before": context_before,
            "context_after": context_after,
        }
        return service.verdict(runtime.state.get("files") or {}, check, fields)

    return StructuredTool.from_function(
        func = run,
        name = "check_patch",
        description = (
            "Checks ONE patch against the page with the application's own rules, with no model, and says whether it would be applied "
            "or why it would be refused and what to change. Call it for each patch before you answer, and fix or drop a refused patch."
        ),
        args_schema = schemas.PatchInput,
    )
