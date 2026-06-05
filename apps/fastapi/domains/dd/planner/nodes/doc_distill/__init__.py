"""doc_distill — per-doc semantic representation for the LLM-first planner.

For corpora ≤80 docs, this node is pass-through (downstream consumes raw
bodies). For 80 < N ≤ 2000, fires parallel LLM calls producing a
1-sentence summary + 5 key terms per doc — compact enough that all
distillates fit into the chapter_propose long-context call.

See docs/DD-PLANNER-LLM-FIRST-SOTA-2026-05-27.md.
"""
from .node import doc_distill
from .service import load_distillates


__all__ = ["doc_distill", "load_distillates"]
