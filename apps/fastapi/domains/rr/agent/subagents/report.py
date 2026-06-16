"""Report subagent — assembles the final ranked digest (SUBAGENTS mode).

Active when `RR_DISCOVERY_MODE=subagents`. The orchestrator dispatches
this LAST. Reads triage + synthesis + extractions from fs, renders a
digest JSON with per-paper cards (arxiv_id, title, 1-line summary,
themes, sources, extraction), and writes it to fs/digest.json.

In tools mode, this subagent is bypassed — the Celery task's
`_build_digest_from_fs` does the assembly in Python.

Augmented with the `digest_rendering` Markdown skill at build time
(architecture-doc §9.2). See agent/skills/digest_rendering.md.

2026-06-15: Bound to `DigestSchema` via SubAgent.response_format. The
DeepAgents ToolStrategy injects a `respond_in_format` tool the LLM must
call to terminate, with Pydantic-validated args. This eliminates the
malformed-JSON failure class (the `Invalid \\uXXXX escape` + `missing
comma` write_digest bounce that burned ~10min/scan). write_digest stays
in the tool list as the side-effect persistence channel; on failure it
no longer bounces the subagent (it returns success-with-warning so the
LLM moves on to respond_in_format).
"""
from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel

from ..keys import SUBAGENT_REPORT
from ..prompts import REPORT_SYSTEM_PROMPT
from ..schemas import DigestSchema
from ..skills import SKILL_DIGEST_RENDERING
from ..tools.fs_tools import (
    list_extractions,
    read_extraction,
    read_synthesis_report,
    read_top_n_papers,
    write_digest,
)


def build_report(model: BaseChatModel) -> dict[str, Any]:
    """SubAgent dict for the report worker."""
    full_prompt = (
        f"=== SKILL: digest_rendering ===\n\n"
        f"{SKILL_DIGEST_RENDERING}\n\n"
        f"=== ROLE ===\n\n"
        f"{REPORT_SYSTEM_PROMPT}\n\n"
        f"=== TERMINATION ===\n\n"
        f"After write_digest persists the digest, call `respond_in_format` "
        f"ONCE with the same payload (Pydantic DigestSchema). The framework "
        f"validates fields on your behalf — emit prose ONLY inside the "
        f"`summary` field, never as your terminal message. write_digest is "
        f"tolerant of partial JSON now; treat any non-ERROR return as "
        f"success and proceed to respond_in_format immediately."
    )
    return {
        "name":          SUBAGENT_REPORT,
        "description": (
            "Assemble the final ranked digest from this scan's triage, "
            "synthesis, and extractions. Outputs a structured JSON "
            "digest with per-paper cards. Persists via write_digest + "
            "terminates via respond_in_format(DigestSchema). Dispatch "
            "LAST, after synthesis. (Active in subagents mode only; "
            "tools mode uses Python assembly in task.py.)"
        ),
        "system_prompt": full_prompt,
        "tools": [
            read_top_n_papers,
            read_synthesis_report,
            list_extractions,
            read_extraction,
            write_digest,
        ],
        "model":           model,
        "response_format": DigestSchema,
    }
