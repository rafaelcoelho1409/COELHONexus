"""Tool and provider bounds for the book content classifier."""

ROTATOR_TASK = "bd_content_page_classifier"
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
