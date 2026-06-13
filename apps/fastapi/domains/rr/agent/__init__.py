"""RR agent package — DeepAgents orchestrator + subagents.

Public API: `build_radar_agent()` returns a compiled DeepAgents agent that
accepts `await agent.ainvoke({"messages": [{"role": "user", "content": "..."}]}, config=...)`.

Imports are deferred to graph.py to keep package-import cheap (the
LLM rotator + MCP client are heavyweight). Callers explicitly:

    from domains.rr.agent.graph import build_radar_agent
"""
