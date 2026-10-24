"""Redis key-builders for the planner package."""
from __future__ import annotations


def cancel_key(thread_id: str) -> str:
    return f"coelhonexus:dd:planner:{thread_id}:cancel"


def event_channel(thread_id: str) -> str:
    return f"coelhonexus:dd:planner:{thread_id}:events"


def snapshot_key(thread_id: str) -> str:
    return f"coelhonexus:dd:planner:{thread_id}:events:snapshot"


def lock_key(slug: str) -> str:
    return f"coelhonexus:dd:planner:lock:{slug}"


def lock_prefix() -> str:
    """Scan-match prefix for `lock_key` — must stay an exact prefix of
    it, or active-run scans silently miss (no error, just empty)."""
    return "coelhonexus:dd:planner:lock:"


def active_run_key(slug: str) -> str:
    return f"coelhonexus:dd:planner:current:{slug}"


def planner_timing_key(slug: str) -> str:
    """MinIO key for the persisted planner timing roll-up — survives a UI
    refresh so the total wall-clock can be shown for a finished run."""
    return f"planner/{slug}/planner-timing-latest.json"

def plan_latest_key(slug: str) -> str:
    """MinIO key for the latest finalized plan (chapter outline + sources).
    Read by synth's run-startup, the FastHTML library view, and debug routes."""
    return f"planner/{slug}/plan-latest.json"
