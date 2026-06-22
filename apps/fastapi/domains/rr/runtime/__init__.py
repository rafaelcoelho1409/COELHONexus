"""Runtime concerns for the RR domain — Redis pub/sub for SSE events.

Mirrors `apps/fastapi/domains/dd/planner/runtime/` shape: `runtime/`
holds the deployment-time concerns (Redis transport, env-builders,
ephemeral coordination) that domain logic (`domain.py`, `service.py`)
doesn't own.

  events.py    publish + subscribe phase events for SSE
  keys.py      redis_url + Redis channel/key builders
  params.py    Redis timeouts + snapshot retention
"""

from .observability import record_phase_event, record_scan_run


__all__ = ["record_phase_event", "record_scan_run"]
