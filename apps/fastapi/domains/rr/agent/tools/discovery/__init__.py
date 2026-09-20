"""Deterministic discovery tools — orchestrator-level Python @tool
wrappers around the 5 source MCP tools (arxiv/semantic_scholar/
huggingface_daily_papers/hn/openalex). Active in "tools" mode; see
../../subagents/service.py for the LLM-driven alternative."""
from __future__ import annotations

from . import domain, service
