"""Pure helpers (no I/O) — the shape of a run's progress record and of the events a run emits."""
from __future__ import annotations
from . import params


def initial_state(run_id: str, now: float) -> dict:
    """A run's progress before it has read anything."""
    return {
        "run_id":     run_id,
        "status":     params.STATUS_QUEUED,
        "completed":  0,
        "total":      0,
        "error":      None,
        "started_at": now,
        "updated_at": now,
    }


def is_active(progress: dict | None) -> bool:
    return bool(progress) and progress.get("status") in params.ACTIVE_STATUSES


def clip(text: str, limit: int = params.ERROR_MAX_CHARS) -> str:
    return text if len(text) <= limit else text[:limit]


def page_event(*, page: dict, markdown: str, completed: int, total: int) -> dict:
    """The event for one extracted page: its manifest entry, its markdown (cut to a size an event may carry) and how far the run is."""
    return {
        "page":      page["page"],
        "markdown":  clip(markdown, params.EVENT_MARKDOWN_MAX_CHARS),
        "truncated": len(markdown) > params.EVENT_MARKDOWN_MAX_CHARS,
        "stats": {key: page[key] for key in ("chars", "headings", "tables", "pictures", "formulas")},
        "figures":      page["figures"],
        "section_path": page["section_path"],
        "completed":    completed,
        "total":        total,
    }
