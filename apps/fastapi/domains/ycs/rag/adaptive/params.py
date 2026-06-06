"""ycs/rag/adaptive — graph-wide tunables.

Mirror of deprecated `graphs/youtube/adaptive.py` inline constants
(`L78, L79, L170, L248, L340`)."""
from __future__ import annotations


# Conversation-history truncation in contextualize (deprecated `L78`).
MAX_HISTORY_TURNS = 5

# Answer-truncation in contextualize prompt formatting (deprecated `L79`).
MAX_HISTORY_ANSWER_CHARS = 300

# Recursion limit passed to sub-graph `ainvoke`s (deprecated `L170, L248`).
SUBGRAPH_RECURSION_LIMIT = 30

# Critic fallback confidence on structured-output error (deprecated `L340`).
CRITIC_FALLBACK_CONFIDENCE = 0.5
