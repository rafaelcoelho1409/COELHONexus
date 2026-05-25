"""sawc_derive constants — module-level tunables only.

Ship #95 (2026-05-24): Analogical Prompting + MPSC derived business
examples for thin subtopics. Adds AI-generated runnable code AFTER
sawc_write commits, BEFORE checklist_eval — only for subtopics whose
vault entry is signature-only or too short to teach effectively.
"""
from __future__ import annotations

import re


# =============================================================================
# Versioning + tunables
# =============================================================================
SAWC_DERIVE_SCHEMA_VERSION = "1.0"
SAWC_DERIVE_PROMPT_VERSION = "v1-analogical-mpsc-2026-05-24"

# MPSC sample count (Multi-Path Self-Consistency, arXiv 2503.04611).
# 3 keeps token budget bounded; majority-of-3 is robust enough for the
# narrow "expand this signature into runnable code" task while still
# letting AST-parse filtering reject 1-2 hallucinations per section.
_N_MPSC_SAMPLES = 3

# Concurrency for derive calls across sections in a chapter. Same
# discipline as sawc_write — bounded fan-out so we don't burst the
# rotator's free-tier pool.
_CONCURRENCY = 4

# Thin-block heuristics: when ALL apply, the subtopic is a derive
# candidate. Tuned to catch signature-only docs (the ch03 failure mode
# in the 2026-05-24 v2 cookbook run) without firing on real examples.
_THIN_MAX_CHARS = 200          # the whole code body
_THIN_MAX_NEWLINES = 2          # 0-2 newlines = single-line signature

# AST-validated derived bodies must land in this LOC band — too short
# = unhelpful, too long = digression. Audit-side hallucination gate
# is in render_audit_write.
_DERIVED_MIN_LINES = 4
_DERIVED_MAX_LINES = 50
_DERIVED_MIN_CHARS = 80
_DERIVED_MAX_CHARS = 4000

# Cap on derive attempts per chapter — burst protection. If the writer
# is consistently producing thin subtopics, we'd rather flag than retry
# 100 times.
_MAX_DERIVES_PER_CHAPTER = 30

# Regex matching a Python function/method signature alone (with no body
# beyond it). Catches the common ch03 pattern:
#   list_skills(client: Client) -> list[SkillSummary]
# Also catches `def foo(...): ...` one-liners.
_SIGNATURE_ONLY_RE = re.compile(
    r"""
    ^\s*
    (?:def\s+|async\s+def\s+)?       # optional def keyword
    \w+\s*                            # function name
    \(.*?\)                           # arg list
    (?:\s*->\s*[\w\[\],\s\|\.]+)?    # optional return annotation
    \s*[:.]?\s*                       # optional trailing : or .
    $
    """,
    re.VERBOSE,
)

# Env flag — default ON. Set KD_ENABLE_SAWC_DERIVE=false to disable
# (the node still runs and emits start/done but skips LLM calls;
# subtopics pass through unchanged).
_ENV_ENABLED = "KD_ENABLE_SAWC_DERIVE"

# dd_process key for the bandit rotator. Lets the bandit learn a
# separate arm-quality posterior for derive (vs writer / critic /
# judge).
_DD_PROCESS = "dd-synth-derive"
# Ship D (2026-05-25) — re-explain pass after a derived sample is
# promoted: regenerates the subtopic's explanation to match the new
# code body. Separate dd_process so the bandit learns a distinct arm-
# quality posterior (this task is shorter + simpler than full derive).
_DD_PROCESS_REEXPLAIN = "dd-synth-derive-reexplain"
_REEXPLAIN_MAX_TOKENS = 400

# Per-call timeouts. Derive is a single short generation per sample
# (~150-400 tokens of code), so we don't need long latency budgets.
_REQUEST_TIMEOUT_S = 60.0
_MAX_OUTPUT_TOKENS = 1200
