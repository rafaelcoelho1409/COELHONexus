"""Provider and tool bounds for the source-figure extractor."""

ROTATOR_TASK = "bd_ingestion_visual_extractor"
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
