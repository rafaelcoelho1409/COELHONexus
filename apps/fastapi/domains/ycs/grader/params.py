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

# Max grader LLM calls in flight at once. 2026-09-15: 2 → 5 — grading
# calls are tiny binary judgments (≪ generate/synthesize payloads) and
# each pass grades up to _PRE_GRADE_CAP=12 docs at 30s each: at 2-wide
# that's 180s per pass dominating every STANDARD cycle, at 5-wide ~75s.
# Still half of DD doc_distill's 10-wide on far heavier calls. Override
# via `KD_GRADER_CONCURRENCY` if needed.
GRADER_CONCURRENCY = 5

# per-call timeout on a single grading invocation. 2026-09-15: 30 →
# 20s tiering — binary relevance judgments return in ~5s median; a slow
# arm should drop the doc fast, not hold a semaphore slot (now 5-wide,
# so a full 12-doc pass costs ~60s worst instead of ~180s).
GRADER_CALL_TIMEOUT_S = 20.0
