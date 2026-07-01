"""chapter_select — greedy coverage selection; writes to chapter_plan_ref matching the legacy reduce_node schema so downstream nodes need no changes."""
from .node import chapter_select


__all__ = ["chapter_select"]
