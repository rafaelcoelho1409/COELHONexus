"""ycs/embeddings — PURE helpers.

Functional Core (`docs/CODE-CONVENTIONS.md` §4): no I/O, no clock,
no logging.

2026-09-13: `is_transient_status`/`backoff_delay_s` removed along with the
manual HTTP retry loop they served — that loop existed because the OLD
design had no server-side failover (hardcoded to one NIM model, so a
retry was the only way to ride out a transient blip). The embedding
endpoint (COELHO LLM Rotator, or whatever's configured) now owns
provider failover itself via its Embedding Curator; a client-side retry
loop on top would just duplicate that policy."""
from __future__ import annotations


def is_empty_input(texts: list[str]) -> bool:
    """The embedding endpoint rejects empty lists AND lists with all-
    empty/whitespace elements with a deterministic 400. Pre-check so we
    don't burn a call on a guaranteed failure."""
    if not texts:
        return True
    return all((not t) or (not t.strip()) for t in texts)
