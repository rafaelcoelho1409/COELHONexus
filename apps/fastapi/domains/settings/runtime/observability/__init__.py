"""settings observability — gen_ai.* spans (spans.py) around the chat +
embeddings endpoint adapters, attribute names in keys.py, metric recorders
in metrics.py."""
from __future__ import annotations
from . import keys, metrics, spans

__all__ = ["keys", "metrics", "spans"]
