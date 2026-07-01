"""Redis key-builders for the planner package."""
from __future__ import annotations

import os
from urllib.parse import quote


def redis_url() -> str:
    """Build the Redis URL from env. Strict reads — secrets must be set."""
    host = os.environ["REDIS_HOST"]
    port = os.environ["REDIS_PORT"]
    password = os.environ["REDIS_PASSWORD"]
    return (
        f"redis://:{password}@{host}:{port}"
        if password else f"redis://{host}:{port}"
    )


def postgres_url() -> str:
    """Build the Postgres URL from env. User+password are percent-encoded
    because POSTGRES_PASSWORD may contain `%`, `&`, `#`, `!`, `^` which
    would break URL parsing as raw chars."""
    user = quote(os.environ["POSTGRES_USER"], safe = "")
    password = quote(os.environ["POSTGRES_PASSWORD"], safe = "")
    host = os.environ["POSTGRES_HOST"]
    port = os.environ["POSTGRES_PORT"]
    db = os.environ["POSTGRES_DATABASE"]
    auth = f"{user}:{password}@" if password else f"{user}@"
    return f"postgresql://{auth}{host}:{port}/{db}"


def cancel_key(thread_id: str) -> str:
    return f"dd:planner:{thread_id}:cancel"


def event_channel(thread_id: str) -> str:
    return f"dd:planner:{thread_id}:events"


def snapshot_key(thread_id: str) -> str:
    return f"dd:planner:{thread_id}:events:snapshot"


def lock_key(slug: str) -> str:
    return f"dd:planner:lock:{slug}"


def active_run_key(slug: str) -> str:
    return f"dd:planner:current:{slug}"


def planner_timing_key(slug: str) -> str:
    """MinIO key for the persisted planner timing roll-up — survives a UI
    refresh so the total wall-clock can be shown for a finished run."""
    return f"planner/{slug}/planner-timing-latest.json"

def plan_latest_key(slug: str) -> str:
    """MinIO key for the latest finalized plan (chapter outline + sources).
    Read by synth's run-startup, the FastHTML library view, and debug routes."""
    return f"planner/{slug}/plan-latest.json"
