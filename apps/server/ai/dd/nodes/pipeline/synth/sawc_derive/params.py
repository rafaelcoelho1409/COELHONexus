"""sawc_derive — tunables (thin-block heuristics, derived-body LOC band, fan-out)."""
from __future__ import annotations

import os


# Concurrency for derive runs across sections in a chapter: bounded fan-out so we don't burst the Rotator's free-tier pool.
CONCURRENCY = 4

# A model call that fails for a transient provider reason is asked again this many times in all, waiting `RETRY_WAIT_S` x attempt between.
ASK_ATTEMPTS = 2
RETRY_WAIT_S = 4.0

# Thin-block heuristics: when ALL apply, the subtopic is a derive candidate. Tuned to catch signature-only docs (the ch03 failure mode
# in a v2 cookbook run) without firing on real examples.
THIN_MAX_CHARS = 200          # the whole code body
THIN_MAX_NEWLINES = 2          # 0-2 newlines = single-line signature

# AST-validated derived bodies must land in this LOC band — too short = unhelpful, too long = digression. The audit-side
# hallucination gate is in render_audit_write.
DERIVED_MIN_LINES = 4
DERIVED_MAX_LINES = 50
DERIVED_MIN_CHARS = 80
DERIVED_MAX_CHARS = 4000

# Cap on derive runs per chapter — burst protection.
MAX_DERIVES_PER_CHAPTER = 30

# An explanation regenerated for derived code: 6-100 words (the schema's own 8-80 floor rejects the subtopic if we miss too far, so this is permissive).
EXPLANATION_MIN_WORDS = 6
EXPLANATION_MAX_WORDS = 100

# Env flag — default ON. Set KD_ENABLE_SAWC_DERIVE=false to disable.
ENV_ENABLED = "KD_ENABLE_SAWC_DERIVE"

BLOB_PREFIX = "synth"


def enabled() -> bool:
    """Default ON; an explicit 'false'/'0'/'no'/'off' in ENV_ENABLED disables it. A function so the module has no import-time env dependency."""
    return (os.environ.get(ENV_ENABLED) or "").strip().lower() in ("", "1", "true", "yes", "on")
