from __future__ import annotations

ROTATOR_TASK = "bd_distill_learning_extractor"
MAX_MODEL_CALLS = 2
MAX_STRUCTURED_RESPONSE_ATTEMPTS = 2
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
