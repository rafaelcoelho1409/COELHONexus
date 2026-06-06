"""ycs/embeddings — PURE retry/backoff helpers.

Functional Core (`docs/CODE-CONVENTIONS.md` §4): no I/O, no clock,
no logging. Mirror of the inline branches in deprecated `_call_api`."""
from __future__ import annotations


def is_empty_input(texts: list[str]) -> bool:
    """NIM rejects empty lists AND lists with all-empty/whitespace
    elements with a deterministic 400. Pre-check so the retry loop
    doesn't block the event loop on a guaranteed failure."""
    if not texts:
        return True
    return all((not t) or (not t.strip()) for t in texts)


def is_transient_status(status_code: int) -> bool:
    """429 or any 5xx — retryable. 4xx (other than 429) — deterministic
    client error, do NOT retry (deprecated comment: retry is useless
    and blocks the event loop)."""
    return status_code == 429 or status_code >= 500


def backoff_delay_s(attempt: int) -> int:
    """Exponential backoff: 2, 4, 8, 16, 32 seconds. Mirror of
    deprecated `wait = 2 ** (attempt + 1)`."""
    return 2 ** (attempt + 1)
