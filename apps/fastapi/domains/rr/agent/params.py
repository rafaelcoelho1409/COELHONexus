"""Tunable parameters for the RR agent.

Per docs/CODE-CONVENTIONS.md §3: a frozen-dataclass groups ≥3 related
tunables so a callsite reads the GROUP, not 5 loose imports. Module-level
PARAMS instance is the canonical accessor.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AgentParams:
    """Top-level agent tunables shared by orchestrator + subagents."""
    # MCP client connection
    mcp_init_timeout_s: float = 30.0

    # Discovery subagent defaults — how many results to request per source
    # (subagent can override per-call via its system-prompt-derived args)
    discovery_n_max: int = 30
    discovery_n_max_hn: int = 50          # HN signal density is lower; cast a wider net

    # Temperature splits — orchestrator is deterministic; subagents allow a
    # touch of freedom for query reformulation when the first probe is empty.
    orchestrator_temperature: float = 0.0
    subagent_temperature: float = 0.0


PARAMS = AgentParams()
