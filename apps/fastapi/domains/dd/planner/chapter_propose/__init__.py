"""chapter_propose — LLM proposes 6-15 candidate chapters covering the
full corpus surface area. Uses structural seeds + doc distillates (or
raw bodies for small N) in a single long-context LLM call.

See docs/DD-PLANNER-LLM-FIRST-SOTA-2026-05-27.md.
"""
from .node import chapter_propose, load_proposals

__all__ = ["chapter_propose", "load_proposals"]
