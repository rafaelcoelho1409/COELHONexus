from __future__ import annotations

ROTATOR_TASK = "bd_distill_research_analyst"
MAX_MODEL_CALLS = 5
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
