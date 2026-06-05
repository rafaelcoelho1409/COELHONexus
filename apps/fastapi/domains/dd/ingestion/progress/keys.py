from __future__ import annotations

import os


def progress_key(run_id: str) -> str:
    return f"dd:runs:{run_id}:progress"


def url_records_key(run_id: str) -> str:
    return f"dd:runs:{run_id}:url_records"


def post_key(run_id: str) -> str:
    return f"dd:runs:{run_id}:post"


def cancel_key(run_id: str) -> str:
    return f"dd:runs:{run_id}:cancel"


def lock_key(framework_slug: str) -> str:
    return f"dd:lock:{framework_slug}"


def redis_url() -> str:
    """URL from REDIS_HOST / REDIS_PORT / REDIS_PASSWORD env vars (password optional)."""
    host = os.environ["REDIS_HOST"].strip()
    port = os.environ["REDIS_PORT"].strip() if "REDIS_PORT" in os.environ else "6379"
    if "REDIS_PASSWORD" in os.environ:
        pwd = os.environ["REDIS_PASSWORD"].strip()
        if pwd:
            return f"redis://:{pwd}@{host}:{port}"
    return f"redis://{host}:{port}"
