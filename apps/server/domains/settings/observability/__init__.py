"""settings observability — gen_ai.* spans and metric recorders around the chat + embeddings
endpoint adapters (service.py); span/attribute names in keys.py."""
from __future__ import annotations
from . import keys, service


__all__ = ["keys", "service"]
