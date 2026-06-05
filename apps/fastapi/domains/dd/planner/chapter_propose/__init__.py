"""chapter_propose — LLM proposes 4-30 candidate chapters covering the
full corpus surface area (target sized to doc count, 2026-05-31). Uses
structural seeds + doc distillates (or raw bodies for small N) in a
single long-context LLM call.

See docs/DD-PLANNER-LLM-FIRST-SOTA-2026-05-27.md.
"""
from .node import chapter_propose
from .service import load_proposals


__all__ = ["chapter_propose", "load_proposals"]
