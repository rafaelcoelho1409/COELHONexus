from __future__ import annotations

ROTATOR_TASK = "bd_distill_report_reviewer"
MAX_MODEL_CALLS = 2
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
