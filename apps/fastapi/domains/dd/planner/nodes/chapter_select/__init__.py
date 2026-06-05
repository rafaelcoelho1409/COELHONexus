"""chapter_select — greedy coverage selection over assigned docs.

Pure-algorithm node (no LLM). Picks the minimum chapter set covering
≥95% of docs above confidence threshold, hard-pinning structurally-
seeded chapters and pruning <3-doc chapters unless pinned.

Output schema matches the legacy reduce_node output (writes to
`chapter_plan_ref`) so downstream order_chapters + plan_write need no
changes.

See docs/DD-PLANNER-LLM-FIRST-SOTA-2026-05-27.md.
"""
from .node import chapter_select


__all__ = ["chapter_select"]
