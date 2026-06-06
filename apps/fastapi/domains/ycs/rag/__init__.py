"""ycs/rag — LangGraph RAG workflows (standard pipeline + adaptive parent).

Direct port of deprecated `graphs/youtube/{rag,adaptive}.py`."""
from .domain import strip_think_tags


__all__ = ["strip_think_tags"]
