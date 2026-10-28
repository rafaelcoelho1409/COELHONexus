"""Redis + MinIO key builders for the synth pipeline."""
from __future__ import annotations


_THREAD_STATE_PREFIX = "coelhonexus:dd:synth:"
_SNAPSHOT_SUFFIX = ":events:snapshot"


def cancel_key(thread_id: str) -> str:
    return f"{_THREAD_STATE_PREFIX}{thread_id}:cancel"


def event_channel(thread_id: str) -> str:
    return f"{_THREAD_STATE_PREFIX}{thread_id}:events"


def snapshot_key(thread_id: str) -> str:
    return f"{_THREAD_STATE_PREFIX}{thread_id}{_SNAPSHOT_SUFFIX}"


def snapshot_pattern(thread_prefix: str) -> str:
    """Scan-match for the snapshot keys of every thread under `thread_prefix` (e.g. `docs-distiller/synth/<slug>/`)."""
    return snapshot_key(f"{thread_prefix}*")


def thread_id_from_snapshot_key(key: str) -> str:
    """Inverse of `snapshot_key`."""
    return key[len(_THREAD_STATE_PREFIX):-len(_SNAPSHOT_SUFFIX)]


def thread_state_pattern(thread_prefix: str) -> str:
    """Scan-match for EVERY redis key (cancel flag, event state, snapshot) of the threads under `thread_prefix`."""
    return f"{_THREAD_STATE_PREFIX}{thread_prefix}*"


def lock_key(slug: str) -> str:
    return f"coelhonexus:dd:synth:lock:{slug}"


def lock_prefix() -> str:
    """Scan-match prefix for `lock_key` — must stay an exact prefix of
    it, or active-run scans silently miss (no error, just empty)."""
    return "coelhonexus:dd:synth:lock:"


def active_study_key(slug: str) -> str:
    return f"coelhonexus:dd:study:current:{slug}"


def active_chapters_key(study_thread_id: str) -> str:
    return f"coelhonexus:dd:study:{study_thread_id}:active_chapters"


def minio_prefix(slug: str) -> str:
    """Every MinIO object of a framework's synth output (what a full wipe of the framework removes)."""
    return f"synth/{slug}/"


def study_timing_key(slug: str) -> str:
    """MinIO key for study timing roll-up — persisted so totals survive a UI refresh."""
    return f"synth/{slug}/study-timing-latest.json"


def chapter_readme_key(slug: str, chapter_id: str) -> str:
    return f"synth/{slug}/{chapter_id}/README.md"


def chapter_render_latest_key(slug: str, chapter_id: str) -> str:
    return f"synth/{slug}/{chapter_id}/render-latest.json"


def book_harmonize_versioned_key(slug: str, manifest_hash: str) -> str:
    return f"synth/{slug}/book_harmonize/{manifest_hash}.json"


def book_harmonize_latest_key(slug: str) -> str:
    return f"synth/{slug}/book_harmonize-latest.json"
