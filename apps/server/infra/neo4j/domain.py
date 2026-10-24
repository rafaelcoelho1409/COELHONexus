"""neo4j domain — pure client-construction helpers (no I/O, deterministic)."""
from __future__ import annotations


def auth_pair(username: str, password: str | None) -> tuple[str, str] | None:
    """`(username, password)` for the async driver, or None for
    unauthenticated access when no password is configured."""
    if password:
        return (username, password)
    return None
