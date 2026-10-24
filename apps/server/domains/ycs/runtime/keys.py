"""ycs/runtime — Postgres URL builder for `app.state.pg_url`, consumed across
YCS (content/admin/agents/query/conversation). Soft `.get(..., default)`
reads (not strict `os.environ[...]`) on purpose — main.py's lifespan wraps
every provision in try/except and degrades to a 5xx-until-reachable warning
instead of crashing boot, so a missing env var here must not raise."""
from __future__ import annotations

import os
from urllib.parse import quote


def postgres_url() -> str:
    # URL-encode user+password; raw % in a password breaks asyncpg DSN parsing with "invalid percent-encoded token".
    user = quote(os.environ.get("POSTGRES_USER", "postgres"), safe = "")
    password = quote(os.environ.get("POSTGRES_PASSWORD", ""), safe = "")
    host = os.environ.get("POSTGRES_HOST", "postgres")
    port = os.environ.get("POSTGRES_PORT", "5432")
    db = os.environ.get("POSTGRES_DB", "postgres")
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"
