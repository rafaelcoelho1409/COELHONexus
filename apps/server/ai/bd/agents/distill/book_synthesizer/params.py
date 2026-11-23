from __future__ import annotations

ROTATOR_TASK = "bd_distill_book_synthesizer"
# One validation correction turn is needed when ToolStrategy reports a schema error.
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
