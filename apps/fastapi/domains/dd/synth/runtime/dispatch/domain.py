"""Pure helpers — no I/O. Used by the resume catch-up path to detect
IMPLEMENTED nodes whose primary output field is empty for the thread.
"""
from __future__ import annotations
import domains
from . import patterns


def chapter_number_from_id(chapter_id: str) -> int:
    m = patterns.CHAPTER_ID_RE.match(chapter_id or "")
    return int(m.group(1)) if m else 0


def missing_implemented_nodes(state: dict) -> list[str]:
    """IMPLEMENTED node names whose primary output field is missing/empty."""
    missing: list[str] = []
    for name in domains.dd.synth.graph.IMPLEMENTED:
        field = domains.dd.synth.graph.NODE_TO_FIELD.get(name)
        if not field:
            continue
        val = state.get(field)
        if val is None or val == "" or val == []:
            missing.append(name)
    return missing
