"""chapter_assign — per-doc LLM membership scoring; multi-assignment allowed, chapter_select breaks ties via greedy coverage."""
from .node import chapter_assign
from .service import load_assignments


__all__ = ["chapter_assign", "load_assignments"]
