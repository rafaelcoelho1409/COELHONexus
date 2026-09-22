"""Runtime concerns for the RR domain — Redis pub/sub for SSE events,
the extraction cache, the fs mirror, the resilient-call helper, the
per-scan LLM counter, and OTel observability.

Mirrors `apps/fastapi/domains/dd/planner/runtime/` shape: `runtime/`
holds the deployment-time concerns (Redis transport, env-builders,
ephemeral coordination) that domain logic (`../domain.py`, `../service.py`)
doesn't own.
"""
from __future__ import annotations
from . import domain, keys, llm_counter, observability, params, service
