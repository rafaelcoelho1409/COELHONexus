"""LLM-driven subagents.

2026-06-12 step-7: 4 discovery subagents + report subagent re-activated.
2026-06-16 (post-f52fb84a): report subagent RETIRED again — it kept
emitting `{` for write_digest. Per-paper theme assignment moved to the
synthesis subagent's `write_synthesis_report.per_paper_themes`. Digest
assembly is now Python-canonical in `task._build_digest_from_fs`.

Active subagents:
  "subagents" mode: discovery_arxiv · discovery_semantic_scholar ·
                    discovery_huggingface_daily_papers · discovery_hn
                    + deep_read + synthesis
  "tools"     mode: deep_read + synthesis (discoveries become tools)

The `report.py` file is kept as a reference + reusable scaffolding (the
DigestSchema and prompt patterns remain useful), but the
`build_report` factory is no longer wired into either topology.
"""
from .deep_read import build_deep_read
from .discovery_arxiv import build_discovery_arxiv
from .discovery_hn import build_discovery_hn
from .discovery_huggingface_daily_papers import build_discovery_huggingface_daily_papers
from .discovery_semantic_scholar import build_discovery_semantic_scholar
from .synthesis import build_synthesis


__all__ = [
    "build_deep_read",
    "build_discovery_arxiv",
    "build_discovery_semantic_scholar",
    "build_discovery_huggingface_daily_papers",
    "build_discovery_hn",
    "build_synthesis",
]
