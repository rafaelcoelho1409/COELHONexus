"""Tool and provider bounds for the book contents reconciler."""

ROTATOR_TASK = "bd_contents_reconciler"
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
INERT_TOOLS = frozenset({"read_file"})
