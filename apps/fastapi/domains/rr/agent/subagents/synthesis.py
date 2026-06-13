"""Synthesis subagent — names themes + finds cross-paper convergence.

The orchestrator dispatches this ONCE after deep_read fan-out completes.
Reads all extractions from the virtual fs + the triage ranking; uses LLM
reasoning to spot what's notable about THIS scan.

Augmented with the `cross_paper_synthesis` Markdown skill at build time
(architecture-doc §9.2). See agent/skills/cross_paper_synthesis.md.
"""
from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel

from ..keys import SUBAGENT_SYNTHESIS
from ..prompts import SYNTHESIS_SYSTEM_PROMPT
from ..skills import SKILL_CROSS_PAPER_SYNTHESIS
from ..tools.fs_tools import (
    list_extractions,
    read_extraction,
    read_top_n_papers,
    write_synthesis_report,
)


def build_synthesis(model: BaseChatModel) -> dict[str, Any]:
    """SubAgent dict for the synthesis worker."""
    full_prompt = (
        f"=== SKILL: cross_paper_synthesis ===\n\n"
        f"{SKILL_CROSS_PAPER_SYNTHESIS}\n\n"
        f"=== ROLE ===\n\n"
        f"{SYNTHESIS_SYSTEM_PROMPT}"
    )
    return {
        "name":          SUBAGENT_SYNTHESIS,
        "description": (
            "Read the deep_read extractions for this scan's top-N papers "
            "and produce a SynthesisReport with: (1) 3-7 emerging themes "
            "spanning ≥2 papers each, (2) cross-paper convergence notes, "
            "(3) a 2-3 sentence executive summary. Persists via the "
            "write_synthesis_report tool. Dispatch ONCE after deep_read."
        ),
        "system_prompt": full_prompt,
        "tools": [
            read_top_n_papers,
            list_extractions,
            read_extraction,
            write_synthesis_report,
        ],
        "model":         model,
    }
