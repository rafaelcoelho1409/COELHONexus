"""redis domain — pure connection-string builder (no I/O, deterministic)."""
from __future__ import annotations

from urllib.parse import quote


def build_url(host: str, port: str | int, password: str) -> str:
    """`redis://[:password@]host:port`. Password is percent-encoded (redis-py
    and kombu both unquote it) and omitted when blank — an empty `:@` segment
    breaks DSN parsing."""
    host = (host or "").strip()
    password = (password or "").strip()
    if password:
        return f"redis://:{quote(password, safe = '')}@{host}:{port}"
    return f"redis://{host}:{port}"
