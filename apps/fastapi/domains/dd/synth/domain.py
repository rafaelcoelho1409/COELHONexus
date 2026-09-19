"""Pure helpers — no I/O."""
from __future__ import annotations


def _slug_from_synth_thread_id(thread_id: str) -> str | None:
    """Extract slug from thread_id (`docs-distiller/synth|study/{slug}/{uuid}`), or None."""
    parts = (thread_id or "").split("/", 3)
    if (
        len(parts) >= 3
        and parts[0] == "docs-distiller"
        and parts[1] in ("synth", "study")
    ):
        return parts[2] or None
    return None
