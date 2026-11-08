"""ycs/ask/rag/adaptive — Adaptive RAG parent graph (FAST/STANDARD/DEEP).

Wraps `domains/ycs/ask/rag/standard` as a sub-graph + adds the FAST
(direct answer) and DEEP (planner → subagents → synthesize → critic)
paths."""
from __future__ import annotations
from . import graph, nodes, params, state
