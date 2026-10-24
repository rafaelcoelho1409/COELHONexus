"""ycs/transcript — Playwright CDP transcript-extraction.
Public surface (verbatim deprecated):
  PlaywrightTranscriptService     — class with init / fetch / close
  get_transcript_service()        — lazy singleton accessor
  init_transcript_service(...)    — async constructor
  close_transcript_service()      — async cleanup
  fetch_transcriptions_batch(...) — cache-aware batch driver
  TranscriptSegment, CaptionTrack — dataclasses
  TranscriptError — base exception"""
from __future__ import annotations
from . import domain, errors, params, service
