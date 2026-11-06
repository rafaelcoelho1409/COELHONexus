ROTATOR_TASK = "bd_finding_verifier"
# The same set as every other Book Distiller role: DeepAgents merges profiles registered under one model name, so a role
# that excluded more would change what the others may call in the same worker process.
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
# `read_file` stays registered, but the permissions below deny every read: this role has no skill and no file to open.
INERT_TOOLS = frozenset({"read_file"})
