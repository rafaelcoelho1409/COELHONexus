"""Pure helpers (no I/O) — what the start gate and the run answer with."""
from __future__ import annotations


def needs_run(manifest: dict | None) -> bool:
    """Whether a book still has pages to extract: it was never extracted, stopped half way or failed."""
    if not manifest:
        return True
    n_pages = manifest.get("n_pages", 0)
    return not (manifest.get("status") == "done" and n_pages > 0 and len(manifest.get("pages", [])) >= n_pages)


def reserved(run_id: str) -> dict:
    return {"status": "reserved", "run_id": run_id}


def queued(book_id: str, run_id: str, resumed: bool) -> dict:
    return {"status": "queued", "book_id": book_id, "run_id": run_id, "resumed": resumed}


def cached(book_id: str) -> dict:
    return {"status": "cached", "book_id": book_id, "run_id": None, "resumed": False}


def locked(book_id: str, holder: str | None) -> dict:
    return {"status": "locked", "book_id": book_id, "run_id": holder, "resumed": False}


def cancel_requested(book_id: str) -> dict:
    return {"book_id": book_id, "status": "cancel_requested"}


def result(book_id: str, run_id: str, status: str, pages: int, error: str | None = None) -> dict:
    """The Celery task's return value."""
    return {"book_id": book_id, "run_id": run_id, "status": status, "pages": pages, "error": error}


def error_text(error: Exception) -> str:
    return f"{type(error).__name__}: {error}"


def snapshot(manifest: dict, progress: dict | None) -> dict:
    return {"manifest": manifest, "progress": progress}
