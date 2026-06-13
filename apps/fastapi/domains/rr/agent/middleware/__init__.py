"""Custom DeepAgents middleware for the RR agent.

Two pieces of cross-cutting behavior that don't fit any single subagent
or tool:

  phase_enforcer.py  Prevents the orchestrator from terminating while
                     phases are still incomplete. Inspects the per-scan
                     fs state and injects a corrective user message
                     instead of letting the agent end.
  phase_events.py    Emits Redis pub/sub events at every model call so
                     the SSE stream has per-phase granularity instead of
                     just the Celery task boundaries.

Both subclass `langchain.agents.middleware.AgentMiddleware`. Wired into
`create_deep_agent(middleware=[...])` in graph.py.
"""
from .phase_enforcer import PhaseEnforcerMiddleware
from .phase_events import PhaseEventsMiddleware


__all__ = ["PhaseEnforcerMiddleware", "PhaseEventsMiddleware"]
