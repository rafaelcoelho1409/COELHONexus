"""Module-level scan-keyed virtual filesystem for the RR agent.

WHY a module dict instead of DeepAgents' built-in StateBackend:

  DeepAgents v0.6 routes its built-in `read_file`/`write_file` tools
  through the StateBackend's `CONFIG_KEY_READ`/`CONFIG_KEY_SEND`
  channels (`backends/state.py`). Custom tools that want to share data
  with subagents have two options:

    1. Hook the same internal channels (fragile — uses `_internal`
       symbols)
    2. Mutate a separate module-level dict and pass `scan_id` to
       partition (simple, opaque to DeepAgents)

  We go with (2). All orchestrator tools and LLM-subagent fs helpers
  receive `scan_id` and route through `fs_read` / `fs_write` below.

CONCURRENCY: the dict is process-local. Each Celery worker is a
separate process (prefork), so one agent run per worker → no
contention. FastAPI HTTP runs (not the production path) would share
a process; the scan_id partition still keeps per-run state isolated.

LIFECYCLE: caller (the Celery task in step 5) calls `init_scan_fs(scan_id)`
before invoking the agent and `clear_scan_fs(scan_id)` in the task's
finally block.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# scan_id → { virtual_path → content (str | dict | list) }
_SCAN_FS: dict[str, dict[str, Any]] = {}


def init_scan_fs(scan_id: str) -> None:
    """Create an empty fs slot for a scan. Idempotent."""
    _SCAN_FS.setdefault(scan_id, {})


def clear_scan_fs(scan_id: str) -> None:
    """Drop the scan's fs. Call in the Celery task's finally to bound memory."""
    _SCAN_FS.pop(scan_id, None)


def fs_write(scan_id: str, path: str, content: Any) -> None:
    """Write a value at `path` within `scan_id`'s fs. Auto-creates the slot
    (covers the test-from-REPL path where init_scan_fs wasn't called)."""
    _SCAN_FS.setdefault(scan_id, {})[path] = content


def fs_read(scan_id: str, path: str, default: Any = None) -> Any:
    """Read the value at `path`; returns `default` (None) if missing."""
    return _SCAN_FS.get(scan_id, {}).get(path, default)


def fs_list(scan_id: str, prefix: str = "") -> list[str]:
    """List paths in this scan's fs that start with `prefix`. Empty prefix → all."""
    keys = _SCAN_FS.get(scan_id, {}).keys()
    return sorted(k for k in keys if k.startswith(prefix))


def fs_has(scan_id: str, path: str) -> bool:
    """Path-exists check."""
    return path in _SCAN_FS.get(scan_id, {})


def fs_snapshot(scan_id: str) -> dict[str, Any]:
    """Shallow copy of a scan's whole fs — debug + smoke-test affordance."""
    return dict(_SCAN_FS.get(scan_id, {}))
