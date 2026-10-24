"""Runtime concerns for the RR domain — Redis pub/sub for SSE events,
the extraction cache, the fs mirror and the resilient-call helper.

Mirrors `apps/server/domains/dd/planner/runtime/` shape: `runtime/`
holds the deployment-time concerns (Redis transport, env-builders,
ephemeral coordination) that domain logic (`../domain.py`, `../service.py`)
doesn't own. OTel observability lives in the sibling `../observability/`.
"""
from __future__ import annotations
from . import domain, keys, params, service
