"""sawc_derive — tunables (MPSC sample count, thin-block heuristics,
derived-body LOC band)."""
from __future__ import annotations


# MPSC (arXiv 2503.04611): N=3 — bounded token budget; majority-of-3 rejects 1-2 AST hallucinations per section.
N_MPSC_SAMPLES = 3

# Concurrency for derive calls across sections in a chapter. Same
# discipline as sawc_write — bounded fan-out so we don't burst the
# rotator's free-tier pool.
CONCURRENCY = 4

# Thin-block heuristics: when ALL apply, the subtopic is a derive
# candidate. Tuned to catch signature-only docs (the ch03 failure mode
# in a v2 cookbook run) without firing on real examples.
THIN_MAX_CHARS = 200          # the whole code body
THIN_MAX_NEWLINES = 2          # 0-2 newlines = single-line signature

# AST-validated derived bodies must land in this LOC band — too short =
# unhelpful, too long = digression. Audit-side hallucination gate is in
# render_audit_write.
DERIVED_MIN_LINES = 4
DERIVED_MAX_LINES = 50
DERIVED_MIN_CHARS = 80
DERIVED_MAX_CHARS = 4000

# Cap on derive attempts per chapter — burst protection.
MAX_DERIVES_PER_CHAPTER = 30

# Env flag — default ON. Set KD_ENABLE_SAWC_DERIVE=false to disable.
ENV_ENABLED = "KD_ENABLE_SAWC_DERIVE"

REEXPLAIN_MAX_TOKENS = 400

# Per-call timeouts. Derive is a single short generation per sample
# (~150-400 tokens of code), so we don't need long latency budgets.
REQUEST_TIMEOUT_S = 60.0
MAX_OUTPUT_TOKENS = 1200


# Optimal-Stopping: ship sample 1 if AST-valid + in band; else fire remaining
# + rank. 2026-09-27: was a KD_SAWC_DERIVE_OPTIMAL_STOPPING env flag — removed
# from Helm after confirming it had been "true" (this same default) since
# introduction with zero history of ever being toggled.
DERIVE_OPTIMAL_STOPPING_ENABLED = True

BLOB_PREFIX = "synth"
