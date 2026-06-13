"""LLM-driven subagents — all 7 active again (step-7 refactor 2026-06-12).

The 4 discovery subagents and the report subagent were retired in step-6
in favor of pure-Python tools. Step-7 brought them back: per
`feedback_rr_learning_purpose`, RR's primary purpose is being a working
DeepAgents reference codebase. Both modes ship; `RR_DISCOVERY_MODE` env
selects which the orchestrator drives:

  "subagents" (default)   — orchestrator dispatches `task(subagent_type=...)`
                            for all 4 discoveries; full DeepAgents loop
  "tools"                 — orchestrator calls discover_*() Python tools
                            directly; faster, no LLM-driven JSON copying

The dormant code is gone. Both paths are first-class.
"""
from .deep_read import build_deep_read
from .discovery_arxiv import build_discovery_arxiv
from .discovery_hn import build_discovery_hn
from .discovery_huggingface_daily_papers import build_discovery_huggingface_daily_papers
from .discovery_semantic_scholar import build_discovery_semantic_scholar
from .report import build_report
from .synthesis import build_synthesis


__all__ = [
    "build_deep_read",
    "build_discovery_arxiv",
    "build_discovery_semantic_scholar",
    "build_discovery_huggingface_daily_papers",
    "build_discovery_hn",
    "build_synthesis",
    "build_report",
]
