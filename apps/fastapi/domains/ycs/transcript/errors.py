"""ycs/transcript — typed exceptions for Playwright CDP transcript extraction.

Deprecated relied on generic `ValueError` / `RuntimeError` (helpers.py:L1352,
L1362, L1525, L1581, L1642). Wrapping those in named types keeps the
deprecated raise sites verbatim where they're in service.py, while letting
upstream handlers (Celery tasks) translate cleanly."""
from __future__ import annotations


class TranscriptError(Exception):
    """Base class for transcript-extraction failures."""


class CDPConnectError(TranscriptError):
    """Failed to (re)connect to the Playwright CDP endpoint after retries."""


class NoTranscriptFoundError(TranscriptError):
    """Video has no transcript (button absent, panel empty, captions
    disabled)."""
