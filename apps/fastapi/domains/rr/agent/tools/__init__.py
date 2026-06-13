"""Orchestrator-level tools for the RR agent.

Per docs/CODE-CONVENTIONS.md §service: deterministic phases that don't
need an LLM (triage / graph_build / persist) live here as LangChain
tools, not as subagents. Tools receive `scan_id` as their first arg
to partition shared state across concurrent agent runs.

  state.py        module-level scan-keyed virtual filesystem + helpers
  fs_tools.py     @tool wrappers around the fs helpers — used by
                  LLM subagents (deep_read · synthesis · report) that
                  can only interact with state via tool calls
  triage.py       pure: read discovery/* → normalize → dedup → score →
                  write triage/top_n.json
  graph_build.py  I/O: read triage/top_n.json + extractions/* → embed
                  → service.persist_paper (Neo4j + Qdrant)
"""
