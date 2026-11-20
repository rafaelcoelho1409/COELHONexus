"""YCS Query agent identity and invocation limits."""

ROTATOR_TASK = "ycs_query_agent"
MAX_MODEL_CALLS = 1
EXCLUDED_TOOLS = frozenset({
    "write_todos",
    "ls",
    "write_file",
    "edit_file",
    "glob",
    "grep",
    "execute",
    "task",
})
