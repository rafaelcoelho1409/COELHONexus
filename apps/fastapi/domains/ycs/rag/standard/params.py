"""ycs/rag/standard — graph-wide tunables.

Mirror of deprecated `graphs/youtube/rag.py` defaults
(`L200, L219`)."""
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
