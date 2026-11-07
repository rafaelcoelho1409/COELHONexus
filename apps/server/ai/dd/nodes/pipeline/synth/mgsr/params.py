"""mgsr — tunable scalars (what a reviewer's decision may ask for)."""
from __future__ import annotations


# The name this stage reports its progress events, cached items and scratch data under.
STEP = "mgsr_replan"


# A chapter at or above this pass rate is not reviewed (matches the checklist's own pass threshold).
PASS_RATE_TRIVIAL = 0.80
# The reviewer's `halt` ("no rewrite would help") is honored only at or above this confidence; below it the chapter is rewritten whole.
HALT_MIN_CONFIDENCE = 0.70
# Sections one iteration may rewrite; a longer list is a whole-chapter rewrite in disguise.
MAX_REVISIONS = 6
INSTRUCTION_MIN_CHARS = 10
INSTRUCTION_MAX_CHARS = 400
RATIONALE_MAX_CHARS = 800

# A model call that fails for a transient provider reason is asked again this many times in all, waiting `RETRY_WAIT_S` x attempt between.
ASK_ATTEMPTS = 2
RETRY_WAIT_S = 4.0

BLOB_PREFIX = "synth"
