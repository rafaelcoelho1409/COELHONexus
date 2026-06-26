"""ycs/transcript — Playwright CDP transcript-extraction.
Public surface (verbatim deprecated):
  PlaywrightTranscriptService     — class with init / fetch / close
  get_transcript_service()        — lazy singleton accessor
  init_transcript_service(...)    — async constructor
  close_transcript_service()      — async cleanup
  fetch_transcriptions_batch(...) — cache-aware batch driver
  TranscriptSegment, CaptionTrack — dataclasses
  TranscriptError, CDPConnectError, NoTranscriptFoundError — exceptions"""
from .domain import (
    CaptionTrack,
    TranscriptSegment,
    classify_error,
)
from .errors import (
    CDPConnectError,
    NoTranscriptFoundError,
    TranscriptError,
)
from .params import (
    CDP_HEADED,
    CDP_HEADLESS,
    MAX_CONCURRENT,
)
from .service import (
    PlaywrightTranscriptService,
    close_transcript_service,
    fetch_transcriptions_batch,
    get_transcript_service,
    init_transcript_service,
)


__all__ = [
    "CDP_HEADED",
    "CDP_HEADLESS",
    "CDPConnectError",
    "CaptionTrack",
    "MAX_CONCURRENT",
    "NoTranscriptFoundError",
    "PlaywrightTranscriptService",
    "TranscriptError",
    "TranscriptSegment",
    "classify_error",
    "close_transcript_service",
    "fetch_transcriptions_batch",
    "get_transcript_service",
    "init_transcript_service",
]
