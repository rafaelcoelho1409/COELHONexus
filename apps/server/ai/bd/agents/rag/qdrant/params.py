"""Qdrant retriever identity and invocation limits."""
from __future__ import annotations

ROTATOR_TASK = "bd_rag_qdrant_retriever"
MAX_MODEL_CALLS = 3
DEFAULT_TOOL_NAMES = frozenset({
    "write_todos",
    "ls",
    "read_file",
    "write_file",
    "edit_file",
    "glob",
    "grep",
    "execute",
    "task",
})
