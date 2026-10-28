"""Pure helpers (no I/O) — the shape of a run's progress, of a recorded URL and of the post-processing summary."""
from __future__ import annotations
from . import params


def initial_state(now: float) -> dict:
    """A run's progress before it has started anything."""
    return {
        "phase":      params.PHASE_INGEST,
        "tier":       None,
        "current":    0,
        "total":      0,
        "last_url":   "",
        "status":     params.STATUS_IDLE,
        "updated_at": now,
    }


def is_active(progress: dict | None) -> bool:
    """Whether a progress record is of a run still in flight."""
    return bool(progress) and progress.get("status") in params.ACTIVE_STATUSES


def url_record(
    url: str,
    *,
    status: str,
    tier: str | None,
    http_code: int | None,
    fetch_ms: int | None,
    bytes_fetched: int | None,
    extracted_chars: int | None,
    error_msg: str | None,
    now: float,
) -> dict:
    """One fetched URL as the run's URL log keeps it (the explorer shows these as the crawl goes)."""
    return {
        "url":             (url or "")[: params.URL_MAX_CHARS],
        "tier":            tier,
        "status":          status,
        "http_code":       http_code,
        "fetch_ms":        fetch_ms,
        "bytes":           bytes_fetched,
        "extracted_chars": extracted_chars,
        "error_msg":       error_msg[: params.ERROR_MAX_CHARS] if error_msg else None,
        "recorded_at":     now,
    }


def post_summary(
    *,
    tier: str | None,
    input_files: int,
    input_bytes: int,
    output_files: int,
    output_bytes: int,
    was_split: bool,
    stubs_dropped: int,
    duplicates_dropped: int,
    notes: str | None,
    now: float,
) -> dict:
    """What post-processing did to the run's pages: how many in, how many out, what it dropped."""
    return {
        "tier":               tier,
        "input_files":        int(input_files),
        "input_bytes":        int(input_bytes),
        "output_files":       int(output_files),
        "output_bytes":       int(output_bytes),
        "expansion_ratio":    float(output_files) / float(input_files) if input_files > 0 else 0.0,
        "was_split":          bool(was_split),
        "stubs_dropped":      int(stubs_dropped),
        "duplicates_dropped": int(duplicates_dropped),
        "notes":              notes[: params.NOTE_MAX_CHARS] if notes else None,
        "recorded_at":        now,
    }
