from __future__ import annotations

# Corpora at-or-below this threshold skip the distillation step (the
# proposer can ingest raw bodies directly). Above this, each doc gets
# a 1-sentence summary + key terms.
_PASS_THROUGH_THRESHOLD = 80

# Per-doc body cap when feeding the summarizer (chars). Generous since
# the summarizer call is cheap; truncating only matters for very long
# README-style docs.
_BODY_CHARS_MAX = 20_000

# Concurrency for parallel doc summarization.
# 2026-05-27 P1 — lowered 16 → 8 after Claude Code Run produced 36%
# rate-limit failures on NIM+Mistral with 16 simultaneous workers. The
# bandit's top-5 cascade kept hitting saturated arms across all 5
# deployments. 8 keeps the burst inside both provider buckets'
# sustained capacity. Wall-time penalty is small (~+30 sec on N=132)
# vs the quality penalty of 48 dropped docs at 16.
_CONCURRENCY = 8

# Summary length bounds (Pydantic-enforced).
_SUMMARY_WORDS_MIN = 8
_SUMMARY_WORDS_MAX = 60
_KEY_TERMS_MIN = 3
_KEY_TERMS_MAX = 8
_KEY_TERM_CHARS_MIN = 2
_KEY_TERM_CHARS_MAX = 80

# LLM call settings.
_MAX_TOKENS = 300
_TEMPERATURE = 0.2

# Repair retries per doc on Pydantic-fail. Higher than usual because
# summaries are short and any miss is cheap to retry.
_MAX_REPAIR_ATTEMPTS = 1

_BLOB_PREFIX = "planner"
# v2 (2026-05-30) — fallback distillate on LLM-distill failure (Fix #4): a
# doc with content but a failed distill is no longer silently dropped from
# the book; it gets a deterministic title/identifier-derived distillate so it
# flows through chapter_assign + chapter_select. Bumped so a re-plan
# re-distills (and applies the fallback) instead of cache-hitting old drops.
_PROMPT_VERSION = "v2-fallback-distill-2026-05-30"
