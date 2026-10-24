"""Pure helpers — no I/O."""
from __future__ import annotations


def _slug_from_planner_thread_id(thread_id: str) -> str | None:
    """`docs-distiller/{slug}/{uuid}` → slug. None on format mismatch."""
    parts = (thread_id or "").split("/", 2)
    if len(parts) >= 2 and parts[0] == "docs-distiller":
        return parts[1] or None
    return None
