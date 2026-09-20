"""Pure functions for RR runtime — no I/O, no event loop, no mocks."""
from __future__ import annotations

import asyncio

from . import params


def is_transient(exc: BaseException) -> bool:
    """True only for errors worth spending another attempt on. Used by
    `service.py`'s `resilient_ainvoke`."""
    if isinstance(exc, asyncio.TimeoutError):
        return True
    msg = str(exc).lower()
    if any(k in msg for k in params.NON_TRANSIENT_SUBSTRINGS):
        return False
    return any(k in msg for k in params.TRANSIENT_SUBSTRINGS)
