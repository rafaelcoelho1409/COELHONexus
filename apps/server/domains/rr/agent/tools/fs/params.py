"""fs params — digest validation tunables (no behavior)."""
from __future__ import annotations


# 50 chars: any legitimate digest is thousands of chars; catches `{`, `}`, `null`, prose snippets.
MIN_DIGEST_JSON_LEN: int = 50
# 2 strikes: if both fail the LLM is structurally confused; surface "give up" so the tool loop exits.
MAX_FAILED_ATTEMPTS: int = 2
