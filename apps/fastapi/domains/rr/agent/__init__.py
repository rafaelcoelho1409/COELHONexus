"""RR agent package — DeepAgents orchestrator + subagents.

Public API: `build_radar_agent()` returns a compiled DeepAgents agent that
accepts `await agent.ainvoke({"messages": [{"role": "user", "content": "..."}]}, config=...)`.

`graph.py` is excluded from this eager chain (same spirit as §8 Exception 1
— it pulls in `deepagents`, well past what a bare `import domains` should
cost). Callers reach it with a direct import:

    from domains.rr.agent.graph import build_radar_agent
"""
from __future__ import annotations

from . import keys, memory, middleware, params, patterns, prompts, schemas, service, skills, subagents, tools
