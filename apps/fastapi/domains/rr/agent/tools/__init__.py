"""Orchestrator-level tools for the RR agent.

Per docs/CODE-CONVENTIONS.md §4/§8: each concern is its own subpackage
with a domain.py (pure) / service.py (I/O) split, mirroring the dd/ycs
"one directory per node" convention.

  state.py        module-level scan-keyed virtual filesystem + helpers
  fs/             @tool wrappers around the fs helpers — used by
                  LLM subagents (deep_read · synthesis · report) that
                  can only interact with state via tool calls
  discovery/      deterministic Python @tool wrappers around the 4
                  source MCP tools (replaces LLM discovery subagents
                  in "tools" mode)
  triage/         pure: read discovery/* → normalize → dedup → score →
                  write triage/top_n.json
  graph_build/    I/O: read triage/top_n.json + extractions/* → embed
                  → domains.rr.service.persist_paper (Neo4j + Qdrant)
  code_synth/     Build-tab paper-extraction → complete Python via a
                  3-round generate/critique/revise loop
"""
from __future__ import annotations
from . import code_synth, discovery, fs, graph_build, state, triage
