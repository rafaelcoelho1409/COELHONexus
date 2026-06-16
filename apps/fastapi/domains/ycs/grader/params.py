"""ycs/grader — per-document char cap + concurrency gate.

`PER_DOC_CHAR_CAP` mirrors deprecated `services/youtube/grader.py:L52`:
caps the transcript-excerpt portion of the grading prompt so a 10k-
token chunk doesn't blow the model's input budget.

`GRADER_CONCURRENCY` (2026-06-15) caps the number of grading LLM calls
in flight at once. The deprecated `asyncio.gather` over all retrieved
documents (typically 9–10 per pass) was firing ~10 simultaneous LLM
calls into whichever free-tier arm the rotator picked — way past the
per-minute rate window the bandit's bandwidth assumes. Result: 80%+
of grader calls returned 429, cascaded retries across providers,
exhausted Gemini's daily quota, and silently failed Ask requests with
empty answers. Sequential-or-near-sequential grading restores the
"small bursts" contract free-tier providers actually honor.

Trade-off: with N=2, grading 10 docs takes ~5 sequential LLM calls
worth of wall-clock instead of 1. On a 4 s/grade model that's ~20 s
vs ~4 s. Acceptable; throughput was already capped at provider rate-
window anyway."""
from __future__ import annotations


PER_DOC_CHAR_CAP = 2000

# Max grader LLM calls in flight at once. 2 = comfortably under any
# free-tier per-minute window; 3 if you're on a paid arm. Override via
# `KD_GRADER_CONCURRENCY` if needed.
GRADER_CONCURRENCY = 2

# 2026-06-15 — per-call timeout on a single grading invocation. Caps
# the cost of one rotator-picked slow model on one document at 30 s
# (about 6× the median grade time). Without this, a hung LLM call
# inside a sub-agent blocks one grader-semaphore slot for the entire
# subgraph run — and the DEEP fan-out has 3 sub-agents × 2 slots = 6
# global slots, so a single hang locks 1/6 of the grading throughput.
# Timeouts surface as `asyncio.TimeoutError` exceptions and are
# treated as "drop this document" by `grade_documents()`.
GRADER_CALL_TIMEOUT_S = 30.0
