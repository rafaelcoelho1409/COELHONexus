"""RR observability — metric recorders (metrics.py) + the shared
`@traced_tool` `execute_tool` span decorator for every `@tool` function
under `domains.rr.agent.tools.*` (service.py). Phase-transition spans stay
in `agent/middleware/service.py` — tightly coupled to
`PhaseEventsMiddleware`'s own state, not centralized here."""
from __future__ import annotations
from . import metrics, service


__all__ = ["metrics", "service"]
