"""LLM-driven subagents for the RR agent — merged into one `service.py`
per docs/CODE-CONVENTIONS.md §8 strict-merge (every builder returns the
same shape: a DeepAgents SubAgent dict).

2026-06-12 step-7: 4 discovery subagents + report subagent re-activated.
2026-06-16 (post-f52fb84a): report subagent RETIRED again — it kept
emitting `{` for write_digest. Per-paper theme assignment moved to the
synthesis subagent's `write_synthesis_report.per_paper_themes`. Digest
assembly is now Python-canonical in `../../task.py::_build_digest_from_fs`.

Active subagents:
  "subagents" mode: discovery_arxiv · discovery_semantic_scholar ·
                    discovery_huggingface_daily_papers · discovery_hn ·
                    discovery_openalex + deep_read + synthesis
  "tools"     mode: deep_read + synthesis (discoveries become tools)

`service.build_report` is kept as a reference + reusable scaffolding (the
DigestSchema and prompt patterns remain useful), but it is no longer
wired into either topology.
"""
from __future__ import annotations

from . import service
