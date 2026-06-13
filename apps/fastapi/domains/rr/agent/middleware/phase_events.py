"""PhaseEventsMiddleware — emits per-phase SSE events to Redis pub/sub.

Today the Celery task emits 3 events per scan: running / persisting / done.
The SSE stream goes idle during the 1-5 min the agent is running. This
middleware hooks every model call + tool dispatch and emits granular
events so the UI status strip can show:

  Phase: discovery (4/4 sources stashed)
  Phase: triage   (66 candidates → top 8)
  Phase: deep_read (3/8 extractions written)
  Phase: synthesis (writing themes...)

Implementation is best-effort: a Redis hiccup logs but does NOT abort the
agent run. emit_event_sync is already used by task.py for the same channel
so the SSE relay code on the FastAPI side needs no change.
"""
from __future__ import annotations

import logging
import re
from typing import Any

try:
    from langchain.agents.middleware import AgentMiddleware
except ImportError:                                                       # pragma: no cover
    from langchain.agents.middleware.types import AgentMiddleware         # type: ignore

from ...runtime.events import emit_event_sync
from ..keys import (
    FS_FILE_DIGEST,
    FS_FILE_SYNTHESIS_REPORT,
    FS_FILE_TRIAGE_TOPN,
)
from ..tools.state import fs_list, fs_read


logger = logging.getLogger(__name__)

_SCAN_ID_RE = re.compile(r"scan_id=([0-9a-fA-F-]{32,})")


class PhaseEventsMiddleware(AgentMiddleware):
    """Emit a Redis pub/sub event reflecting the agent's current phase."""

    name: str = "rr_phase_events"

    def __init__(self) -> None:
        super().__init__()
        # Per-scan last (phase, message) pair emitted. Dedup must include the
        # message — within the discovery phase the message ticks 0/4 → 4/4,
        # and the UI relies on those granular updates. Dedup by phase alone
        # would swallow them and the strip would freeze on "0/4 sources stashed"
        # until phase=triage fires.
        self._last_emit: dict[str, tuple[str, str]] = {}

    @staticmethod
    def _scan_id_from_state(state: dict[str, Any]) -> str | None:
        messages = state.get("messages", []) or []
        for m in messages:
            content = getattr(m, "content", "") or ""
            if not isinstance(content, str):
                continue
            m2 = _SCAN_ID_RE.search(content)
            if m2:
                return m2.group(1)
        return None

    @staticmethod
    def _current_phase(scan_id: str) -> tuple[str, str]:
        """(phase_name, message) — what the agent is doing RIGHT NOW based
        on what's in fs. Same precedence as the phase enforcer."""
        n_discoveries = len(fs_list(scan_id, prefix="discovery/"))
        if n_discoveries < 4:
            return "discovery", f"{n_discoveries}/4 sources stashed"
        if fs_read(scan_id, FS_FILE_TRIAGE_TOPN) is None:
            return "triage", "ranking + dedup"
        n_extractions = len(fs_list(scan_id, prefix="extractions/"))
        topn = fs_read(scan_id, FS_FILE_TRIAGE_TOPN) or []
        if n_extractions < len(topn):
            return "deep_read", f"{n_extractions}/{len(topn)} extractions written"
        if fs_read(scan_id, FS_FILE_SYNTHESIS_REPORT) is None:
            return "synthesis", "clustering themes"
        if fs_read(scan_id, FS_FILE_DIGEST) is None:
            return "report", "assembling digest"
        return "done", "agent finished — task is persisting"

    def after_model(self, state: dict[str, Any]) -> dict[str, Any] | None:
        scan_id = self._scan_id_from_state(state)
        if not scan_id:
            return None
        phase, message = self._current_phase(scan_id)
        if self._last_emit.get(scan_id) == (phase, message):
            return None
        self._last_emit[scan_id] = (phase, message)
        # Best-effort — emit_event_sync is sync + catches its own errors.
        emit_event_sync(scan_id, phase, message=message)
        return None
