"""Pure helpers (no I/O) — best-source pick from a catalog entry, what a tier is handed, and the shape of every answer the
dispatcher gives (the gate's, the run's, its metrics)."""
from __future__ import annotations
from . import params

from dataclasses import asdict



def pick_best(entry: dict) -> dict | None:
    for kind in params.KIND_PRIORITY:
        if entry.get(kind):
            return {"kind": kind, "url": entry[kind]}
    return None


def source_chain(entry: dict) -> list[dict]:
    """Every source the catalog entry declares, best first — `[{kind, url}, ...]`. The dispatcher walks it: the first
    entry is the plan, the rest are what it falls back on when a tier turns out to be unusable."""
    return [{"kind": kind, "url": entry[kind]} for kind in params.KIND_PRIORITY if entry.get(kind)]


def tier_arguments(kind: str, entry: dict) -> dict:
    """What the tier of source `kind` is handed from the catalog `entry`, besides its URL, the slug, the progress and the store
    (`params.TIER_ARGUMENTS`): a field the entry lacks is `None`."""
    return {argument: entry.get(field) for argument, field, kinds in params.TIER_ARGUMENTS if kind in kinds}


def session_id(slug: str, run_id: str) -> str:
    """The Langfuse session of a run: the same `docs-distiller/{slug}/…` label Planner's and Synth's use, so a framework's whole
    pipeline is found under one name. Only a label — Redis, MinIO and the REST routes keep the plain `run_id`."""
    return f"docs-distiller/{slug}/{run_id}"


def run_attributes(run_id: str, slug: str) -> dict:
    """The attributes of a run's span."""
    return {
        "dd.domain":                params.RUN_KIND,
        "dd.run.kind":              params.RUN_KIND,
        "ingestion.run_id":         run_id,
        "ingestion.framework_slug": slug,
    }


def run_metrics(result: dict) -> dict:
    """The labels and counts of a finished run's metric, from the result the dispatcher returned."""
    post = result.get("post") or {}
    return {
        "tier_kind":    str(result.get("tier_kind") or params.UNKNOWN),
        "outcome":      str(result.get("status") or params.UNKNOWN),
        "output_files": int(post.get("output_files", 0) or 0),
        "output_bytes": int(post.get("output_bytes", 0) or 0),
    }


# ---- the gate's answers (what POST /runs says when a run is asked for) --------------------------------------------------------
def locked_by_other(slug: str, running_slug: str, running_run_id: str) -> dict:
    return {
        "status": "locked",
        "slug": running_slug,
        "run_id": running_run_id,
        "message": (
            f"Another ingestion is running ({running_slug!r}, "
            f"run_id={running_run_id}). Wait for it to finish or "
            f"cancel it before starting {slug!r}."
        ),
    }


def locked_by_same(slug: str, run_id: str) -> dict:
    return {
        "status": "locked",
        "slug": slug,
        "run_id": run_id,
        "message": (
            f"An ingestion of {slug!r} is already running "
            f"(run_id={run_id}). Wait for it to finish or cancel "
            f"it before triggering another."
        ),
    }


def locked_by_race(slug: str, held_run_id: str | None) -> dict:
    return {
        "status": "locked",
        "slug": slug,
        "run_id": held_run_id or "?",
        "message": "Concurrent acquire race; try again.",
    }


def cached(slug: str, manifest) -> dict:
    return {"status": "cached", "slug": slug, "run_id": None, "manifest": manifest}


def reserved(slug: str, run_id: str) -> dict:
    return {"status": "reserved", "slug": slug, "run_id": run_id}


def queued(slug: str, run_id: str) -> dict:
    """What POST /runs answers once the run is handed to the worker."""
    return {"status": "queued", "slug": slug, "run_id": run_id}


def cancel_requested(run_id: str) -> dict:
    return {"run_id": run_id, "status": "cancel_requested"}


def run_snapshot(run_id: str, progress, manifest, post) -> dict:
    """What GET /runs/{id} answers for a run in flight: its progress, the manifest so far and the post-processing summary."""
    return {"run_id": run_id, "progress": progress, "manifest": manifest, "post": post}


# ---- the run's answers (what the dispatcher returns, and the Celery task result carries) --------------------------------------
def base_result(run_id: str, slug: str, entry: dict, best: dict) -> dict:
    """What every answer of a run that got as far as picking a source carries."""
    return {
        "run_id": run_id,
        "slug": slug,
        "framework_name": entry["name"],
        "tier_kind": best["kind"],
        "tier_url": best["url"],
    }


def rejected_result(run_id: str, slug: str, error: str, **known: str) -> dict:
    """A run that could not start (an unknown slug, a catalog entry with no source), with whatever is known of it."""
    return {"run_id": run_id, "slug": slug, **known, "status": "failed", "error": error}


def error_text(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"


def done_result(base: dict, manifest: list, post_summary: dict) -> dict:
    return {
        **base,
        "status": "done",
        "pages_written": len(manifest),
        "post": post_summary,
        "manifest": [asdict(e) for e in manifest],
    }


def cancelled_result(base: dict) -> dict:
    return {**base, "status": "cancelled", "pages_written": 0}


def failed_result(base: dict, error: BaseException) -> dict:
    return {**base, "status": "failed", "error": error_text(error)}


def post_record(tier_kind: str, post_summary: dict) -> dict:
    """The post-processing summary as `Progress.record_post` takes it."""
    return {
        "tier": tier_kind,
        "input_files": post_summary["input_files"],
        "input_bytes": post_summary["input_bytes"],
        "output_files": post_summary["output_files"],
        "output_bytes": post_summary["output_bytes"],
        "was_split": post_summary["was_split"],
        "stubs_dropped": post_summary["stubs_dropped"],
        "duplicates_dropped": post_summary["duplicates_dropped"],
    }


def finalize_extra(entry: dict, base: dict, run_id: str, index_summary: str) -> dict:
    """What a finished run adds to the stored manifest. `index_summary` is Tier 2's llms.txt blockquote ("" for every other tier;
    readers must `.get()` with a fallback)."""
    return {
        "framework_name": entry["name"],
        "tier_kind":      base["tier_kind"],
        "tier_url":       base["tier_url"],
        "run_id":         run_id,
        "index_summary":  index_summary or "",
    }
