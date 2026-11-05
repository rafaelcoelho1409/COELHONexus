"""Pure rules for DD Agentic AI candidates: run ids, graph thread names, input digests, manifest rewrites. I/O lives in service.py."""

from __future__ import annotations
import domains
from . import params, patterns

import hashlib
import json
from dataclasses import asdict
from typing import Any


def is_valid_run_id(run_id: str) -> bool:
    return bool(patterns.RUN_ID_RE.fullmatch(run_id))


def is_resumable(progress: Any) -> bool:
    """Whether a saved progress record belongs to a run that ended failed or cancelled."""
    return (
        isinstance(progress, dict)
        and progress.get("status") in params.RESUMABLE_STATUSES
    )


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def candidate_digest(refs: list[tuple[int, str]]) -> str:
    """Short digest of a candidate's frozen page inputs, one `(idx, source_sha256)` per page."""
    encoded = json.dumps(refs, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[: params.DIGEST_CHARS]


def thread_id(slug: str, run_id: str, digest: str) -> str:
    """The LangGraph thread a candidate's checkpoints live under."""
    return f"dd-ingestion:{slug}:{run_id}:{digest}"


def candidate_pages_by_idx(candidate: Any) -> dict[Any, dict]:
    """The pages of a published candidate manifest, keyed by page index; empty for an unpublished one."""
    pages = candidate.get("pages", []) if isinstance(candidate, dict) else []
    return {page.get("idx"): page for page in pages if isinstance(page, dict)}


def page_refs_of(candidate_pages: dict[Any, dict]) -> list[tuple[int, str]]:
    """`(idx, source_sha256)` of every candidate page that recorded both."""
    return [
        (page["idx"], page["source_sha256"])
        for page in candidate_pages.values()
        if isinstance(page, dict)
        and isinstance(page.get("idx"), int)
        and isinstance(page.get("source_sha256"), str)
    ]


def manifest_extra(existing: dict, framework_name: str, run_id: str) -> dict:
    """Framework-manifest fields a finalized candidate carries over, plus its own run id."""
    extra = {
        key: value
        for key, value in existing.items()
        if key not in params.MANIFEST_OWNED_FIELDS
    }
    extra["agentic_run_id"] = run_id
    extra.setdefault("framework_name", framework_name)
    extra.setdefault("run_id", existing.get("run_id", run_id))
    return extra


def original_source_key(slug: str, run_id: str, entry: Any, page: dict) -> str:
    """Where a page's pre-AI body was frozen: its manifest entry, else its candidate record, else the input key."""
    source_key = entry.source_key or page.get("source_key")
    if isinstance(source_key, str) and source_key:
        return source_key
    return domains.dd.ingestion.storage.keys.agentic_input_key(
        slug,
        run_id,
        entry.idx,
        entry.slug,
    )


def restored_manifest(manifest: dict, entries: list, ingested_at: float) -> dict:
    """The framework manifest once a candidate's pages are swapped back for their pre-AI versions."""
    return {
        **manifest,
        "entries": [asdict(entry) for entry in entries],
        "page_count": len(entries),
        "total_bytes": sum(entry.bytes for entry in entries),
        "ingested_at": ingested_at,
        "agentic_run_id": None,
    }
