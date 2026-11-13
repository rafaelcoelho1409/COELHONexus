"""sawc_derive — limits, defaults, and tunables for derivation and capstone generation."""
from __future__ import annotations

import builtins
import os


# The name this stage reports its progress events, cached items and scratch data under.
STEP = "sawc_derive"


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

# The capstone that ends a tutorial chapter: its heading, its size in non-blank lines, the share of the chapter's steps whose key API it must use, and how
# much of the chapter's code the designer is shown (per example, then in all).
CAPSTONE_PREFIX = "Capstone"
CAPSTONE_MIN_LINES = 12
CAPSTONE_MAX_LINES = 120
CAPSTONE_MIN_USE_SHARE = 0.6
# A chapter that teaches fewer examples than this (a reference chapter) has nothing to combine: it gets no capstone, and its judge does not ask for one.
CAPSTONE_MIN_EXAMPLES = 3
CAPSTONE_BLOCK_CHARS = 1_500
CAPSTONE_CODE_CHARS = 14_000
CAPSTONE_MAX_PARTS = 4
CAPSTONE_CITATIONS = 3
INTRO_MIN_CHARS = 20
INTRO_MAX_CHARS = 400
COMMON_API_NAMES = frozenset(dir(builtins)) | frozenset({
    "append", "extend", "get", "items", "keys", "values", "join", "format", "split", "strip", "lower", "upper", "replace", "update", "copy", "pop",
    "run", "main", "start", "close", "read", "write", "load", "dump", "dumps", "loads",
})
EMPTY_CAPSTONE_INTRO = "This application combines the steps of the chapter into one program that runs."
EMPTY_CAPSTONE_PART = "This part uses the code of the earlier steps."

# Env flag — default ON. Set KD_ENABLE_SAWC_DERIVE=false to disable.
ENV_ENABLED = "KD_ENABLE_SAWC_DERIVE"

BLOB_PREFIX = "synth"


def enabled() -> bool:
    """Default ON; an explicit 'false'/'0'/'no'/'off' in ENV_ENABLED disables it. A function so the module has no import-time env dependency."""
    return (os.environ.get(ENV_ENABLED) or "").strip().lower() in ("", "1", "true", "yes", "on")
