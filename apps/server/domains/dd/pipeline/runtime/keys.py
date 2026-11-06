"""Redis key builders for the planner's run machinery; the strings are stored state, so they keep the stage name."""
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
    """Scan-match prefix for `lock_key` — must stay an exact prefix of it, or active-run scans silently miss (no error, just empty)."""
    return "coelhonexus:dd:planner:lock:"


def active_run_key(slug: str) -> str:
    return f"coelhonexus:dd:planner:current:{slug}"
