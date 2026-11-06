"""Bounds shared by every Docs Distiller deep agent."""

# The tools Deep Agents' default stack puts in front of a model; a role is given only the ones it needs.
STACK_TOOLS = frozenset({
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
READ_TOOLS = frozenset({"ls", "read_file", "glob", "grep"})

# Model calls one agent run may make before it ends without an answer (a loop that never finishes costs the run its deadline).
MODEL_CALL_LIMIT = 8
# Transient provider failures (timeout, dropped connection, 429, 5xx) are retried by the agent itself.
MODEL_RETRIES = 2
MODEL_RETRY_DELAY_S = 4.0
MODEL_RETRY_BACKOFF = 2.0
MODEL_RETRY_MAX_DELAY_S = 30.0
# Tool calls one agent run may make; a refused call goes back to the model as a message and the run goes on.
TOOL_CALL_LIMIT = 40
# What the model sees of a tool failure.
TOOL_ERROR_CHARS = 300
