from __future__ import annotations


def progress_key(run_id: str) -> str:
    return f"coelhonexus:dd:runs:{run_id}:progress"


def url_records_key(run_id: str) -> str:
    return f"coelhonexus:dd:runs:{run_id}:url_records"


def post_key(run_id: str) -> str:
    return f"coelhonexus:dd:runs:{run_id}:post"


def cancel_key(run_id: str) -> str:
    return f"coelhonexus:dd:runs:{run_id}:cancel"


def lock_key(framework_slug: str) -> str:
    return f"coelhonexus:dd:lock:{framework_slug}"
