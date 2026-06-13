"""Report subagent — assembles the final ranked digest (SUBAGENTS mode).

Active when `RR_DISCOVERY_MODE=subagents`. The orchestrator dispatches
this LAST. Reads triage + synthesis + extractions from fs, renders a
digest JSON with per-paper cards (arxiv_id, title, 1-line summary,
themes, sources, extraction), and writes it to fs/digest.json.

In tools mode, this subagent is bypassed — the Celery task's
`_build_digest_from_fs` does the assembly in Python.

Augmented with the `digest_rendering` Markdown skill at build time
(architecture-doc §9.2). See agent/skills/digest_rendering.md.
"""
from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel

from ..keys import SUBAGENT_REPORT
from ..prompts import REPORT_SYSTEM_PROMPT
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
        f"{REPORT_SYSTEM_PROMPT}"
    )
    return {
        "name":          SUBAGENT_REPORT,
        "description": (
            "Assemble the final ranked digest from this scan's triage, "
            "synthesis, and extractions. Outputs a structured JSON "
            "digest with per-paper cards. Persists via write_digest. "
            "Dispatch LAST, after synthesis. (Active in subagents mode "
            "only; tools mode uses Python assembly in task.py.)"
        ),
        "system_prompt": full_prompt,
        "tools": [
            read_top_n_papers,
            read_synthesis_report,
            list_extractions,
            read_extraction,
            write_digest,
        ],
        "model":         model,
    }
