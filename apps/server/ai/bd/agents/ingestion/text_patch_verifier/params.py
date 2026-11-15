ROTATOR_TASK = "bd_ingestion_text_patch_verifier"
FORMAT_RETRIES = 1
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
