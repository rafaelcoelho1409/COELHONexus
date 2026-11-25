"""Search retriever identity and invocation limits."""
from __future__ import annotations

ROTATOR_TASK = "bd_rag_web_search_retriever"
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
