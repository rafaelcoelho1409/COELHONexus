"""Reusable Markdown skill bundles for RR subagents (DeepAgents §9.2 pattern).

Each `.md` file in this directory captures a versionable, prompts.py-free
"how to do X" that gets merged into one or more subagent system_prompts.
Same skill loads into multiple subagents (e.g. `digest_rendering` is used
by both the `report` subagent and a future FastHTML "explain this paper"
affordance — they share one source of truth).

This mirrors `create_deep_agent(skills=[...])` in spirit. DeepAgents v0.6's
native skill loading isn't fully documented yet, so we do it ourselves
here — the loader (`loader.py`) reads each .md and the subagent builders
prepend the content to their system_prompt.

When DeepAgents stabilizes its `skills=` parameter, swap to that and keep
the .md files in place.
"""
from .loader import (
    SKILL_ARXIV_QUERY_SHAPING,
    SKILL_CROSS_PAPER_SYNTHESIS,
    SKILL_DIGEST_RENDERING,
    SKILL_PAPER_EXTRACTION,
    SKILL_ROTATOR_ETIQUETTE,
    load_skill,
)


__all__ = [
    "SKILL_ARXIV_QUERY_SHAPING",
    "SKILL_CROSS_PAPER_SYNTHESIS",
    "SKILL_DIGEST_RENDERING",
    "SKILL_PAPER_EXTRACTION",
    "SKILL_ROTATOR_ETIQUETTE",
    "load_skill",
]
