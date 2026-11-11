"""Compatibility facade for the YCS Ask graph now owned by ``ai.ycs``."""
from __future__ import annotations


def build_adaptive_rag_graph(*args, **kwargs):
    """Delegate legacy callers to the application-specific AI graph."""
    import ai

    return ai.ycs.graphs.ask.adaptive.graphs.build_adaptive_rag_graph(*args, **kwargs)
