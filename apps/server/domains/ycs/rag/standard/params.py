"""ycs/rag/standard — graph-wide tunables."""
from __future__ import annotations


# Conditional-edge ceiling for the rewrite → retrieve loop. The
# `RunnableConfig` can override via `configurable.max_retries` per
# request; this is the fallback.
DEFAULT_MAX_RETRIES = 3

# Recursion limit passed to the standard graph's `ainvoke` calls
# (deprecated `adaptive.py:L170, L248`). Headroom for: 1 retrieve +
# 1 grade + (1 generate + 1 hallucination + 1 rewrite) × MAX_RETRIES
# + cite = ~3 + 3×3 + 1 = ~13, doubled for safety.
DEFAULT_RECURSION_LIMIT = 30


# Source-card snippet cap (Perplexity/ChatGPT-search convention ~200
# chars). Capped client-side too (`renderCitation` in ask.js) as a
# defensive second layer, but the truncation is authored here so the
# payload itself stays small over SSE.
CITE_SNIPPET_CHAR_CAP = 220

# Own budget for the corroborate node, separate from other judges — it
# does a search THEN a judge call, so it needs headroom for both, but
# this is still a rare best-effort safety net, not a path worth
# waiting on indefinitely.
CORROBORATE_JUDGE_TIMEOUT_S = 30.0

# Tighter than `generate`'s 180s — soft-evidence prompts run shorter
# context. 2026-09-15: 90 → 60s tiering — this is the last-resort
# answer; waiting 90s+ here is the worst UX on the path.
FALLBACK_TIMEOUT_S = 60.0

# Cap on soft-evidence docs passed into the prompt context. The state
# field is capped at 12 across all rewrite rounds; this is the
# per-call slice the LLM actually reads. 8 keeps total prompt size
# near 4 KB so every free-tier arm's window stays comfortable.
FALLBACK_SOFT_EVIDENCE_FOR_PROMPT = 8

# Cap on "related videos" citations surfaced in the right-rail. 6
# matches the typical `format_citations` payload size — more would
# overwhelm the rail UI, fewer would feel sparse.
FALLBACK_RELATED_CITATIONS_CAP = 6


# Generate-node budgets (see node.py): 180s ceiling for hung
# connections after catalog exhaustion; per-doc/total context caps
# mirroring the grader's slice so slow arms complete instead of
# timing out on unbounded context.
GENERATE_TIMEOUT_S = 180.0
GENERATE_PER_DOC_CHARS = 2000
GENERATE_TOTAL_CHARS = 12000

# Pre-grade merge cap: prompt's input budget is 12 docs × ~500
# chars/doc ≈ 6 KB context. Higher cap risks token bloat with no
# recall gain — the retriever ranks within each round already.
RETRIEVE_PRE_GRADE_CAP = 12

# 2026-09-15: 30 → 20s tiering — failure falls back to
# "{question} (expanded)", so fail fast.
REWRITE_TIMEOUT_S = 20.0
