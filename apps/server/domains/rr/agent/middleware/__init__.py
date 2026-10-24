"""Custom DeepAgents middleware for the RR agent — PhaseEnforcerMiddleware
+ PhaseEventsMiddleware, merged into one `service.py` (both are
`AgentMiddleware` subclasses — same role). Wired into
`create_deep_agent(middleware=[...])` in ../graph.py."""
from __future__ import annotations
from . import service
