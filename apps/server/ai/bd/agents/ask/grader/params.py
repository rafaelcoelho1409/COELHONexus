"""BD Ask grader identity and call limits."""

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

ROTATOR_TASK = "bd_ask_evidence_grader"
MAX_MODEL_CALLS = 2
