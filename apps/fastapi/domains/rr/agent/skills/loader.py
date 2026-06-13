"""Skill loader — reads .md skill files from this directory.

Pattern: each .md file lives next to this loader.py. The constants below
hold the loaded content (one-time read at module import). Subagent builders
import the constant they need and prepend it to their system_prompt.

Why module-level constants vs runtime load: the .md files are part of the
deployed Python package (Skaffold ships them via the apps/fastapi COPY).
They don't change between deploys — loading them eagerly at import keeps
subagent-build paths simple + deterministic.
"""
from __future__ import annotations

from pathlib import Path


_SKILLS_DIR = Path(__file__).parent


def load_skill(name: str) -> str:
    """Return the content of `<name>.md` from this directory.

    Raises FileNotFoundError if the skill doesn't exist — surfaces typos
    at module-import time rather than letting an empty-string skill
    silently dilute a subagent's prompt.
    """
    path = _SKILLS_DIR / f"{name}.md"
    if not path.is_file():
        raise FileNotFoundError(
            f"[rr-skills] skill {name!r} not found at {path}"
        )
    return path.read_text(encoding="utf-8").strip()


# Module-level loads — fail fast at import if any .md file is missing/typoed.
SKILL_PAPER_EXTRACTION       = load_skill("paper_extraction")
SKILL_CROSS_PAPER_SYNTHESIS  = load_skill("cross_paper_synthesis")
SKILL_DIGEST_RENDERING       = load_skill("digest_rendering")
SKILL_ARXIV_QUERY_SHAPING    = load_skill("arxiv_query_shaping")
SKILL_ROTATOR_ETIQUETTE      = load_skill("rotator_etiquette")
