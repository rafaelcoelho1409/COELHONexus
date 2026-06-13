"""Deep-read subagent — extracts {problem, method, math, how_to_build,
money_angle, confidence} for ONE paper.

The orchestrator dispatches this subagent in parallel (one `task` call per
top-N paper). Each instance gets an isolated context — the DeepAgents
subagent-isolation payoff.

Augmented with the `paper_extraction` Markdown skill at build time
(architecture-doc §9.2 pattern — see agent/skills/paper_extraction.md
for the reusable "how to extract" reference).
"""
from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel

from ..keys import SUBAGENT_DEEP_READ
from ..prompts import DEEP_READ_SYSTEM_PROMPT
from ..skills import SKILL_PAPER_EXTRACTION
from ..tools.fs_tools import read_top_n_papers, write_extraction


def build_deep_read(model: BaseChatModel) -> dict[str, Any]:
    """SubAgent dict for the deep_read worker.

    System prompt = `paper_extraction` skill content + the deep_read
    glue prompt. The skill provides the field rubrics + failure-mode
    warnings; the glue tells the LLM what tools to use.
    """
    full_prompt = (
        f"=== SKILL: paper_extraction ===\n\n"
        f"{SKILL_PAPER_EXTRACTION}\n\n"
        f"=== ROLE ===\n\n"
        f"{DEEP_READ_SYSTEM_PROMPT}"
    )
    return {
        "name":          SUBAGENT_DEEP_READ,
        "description": (
            "Read ONE paper (arxiv_id provided in the task description, "
            "abstract loaded from fs/triage/top_n.json) and produce a "
            "structured 5-field extraction. Persists via write_extraction. "
            "Dispatch one per paper, in parallel for Phase 3 fan-out."
        ),
        "system_prompt": full_prompt,
        "tools":         [read_top_n_papers, write_extraction],
        "model":         model,
    }
