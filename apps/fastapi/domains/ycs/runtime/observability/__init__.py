"""YCS observability — node-level OTel spans (service.py) + retrieval-tier
db.* / gen_ai.* spans (spans.py) + Ask metric recorders (metrics.py)."""
from __future__ import annotations

from . import metrics, service, spans
