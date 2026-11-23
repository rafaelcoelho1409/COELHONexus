from __future__ import annotations

ROTATOR_TASK = "bd_distill_report_reviewer"
# One validation correction turn is needed when ToolStrategy reports a schema error.
MAX_MODEL_CALLS = 2
PROVIDER_CALL_ATTEMPTS = 1
OUTPUT_REPAIR_ATTEMPTS = 2
TOTAL_TIMEOUT_S = 90.0
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
