"""PhaseEnforcerMiddleware — keeps the orchestrator running until phases complete.

The orchestrator's LLM often emits a few discovery tool_calls and then
returns a "looks good" summary message without calling triage_candidates /
deep_read / etc. DeepAgents accepts that as termination, the agent exits,
and we end up at the `_build_digest_from_fs` fallback with no extractions.

This middleware inspects the per-scan fs state after each model call.
When it sees the agent trying to END (no tool_calls in the AIMessage AND
not the response_format payload) while fs is incomplete (e.g. discoveries
done but triage NOT done), it injects a corrective HumanMessage saying
"you haven't called X yet — call it now" and forces another loop.

Failure modes if used incorrectly:
  - Forces the orchestrator into an infinite loop if the corrective
    message never lands. Cap at MAX_CORRECTIONS retries (default 6).
  - Misreads end-of-conversation if a phase legitimately produced zero
    output. Use fs presence (not item count) to decide phase completion.

This is a textbook DeepAgents Middleware pattern — same shape as
`langchain.agents.middleware.todo.TodoListMiddleware` (we observed it
in the ainvoke stack on smoke runs).
"""
from __future__ import annotations

import logging
import re
from typing import Any

try:
    # langchain v1.x location
    from langchain.agents.middleware import AgentMiddleware
except ImportError:                                                       # pragma: no cover
    # Fallback path — different langchain release may keep it elsewhere
    from langchain.agents.middleware.types import AgentMiddleware         # type: ignore

from langchain_core.messages import HumanMessage

from ..keys import (
    FS_FILE_DIGEST,
    FS_FILE_SYNTHESIS_REPORT,
    FS_FILE_TRIAGE_TOPN,
)
from ..tools.state import fs_list, fs_read


logger = logging.getLogger(__name__)


# Each correction = one extra LLM turn. Cap at MAX so a broken corrective
# loop doesn't burn the entire rate budget.
MAX_CORRECTIONS = 6


# Lift `scan_id=<uuid>` out of the orchestrator's message history so the
# middleware can read the right fs slot. Same regex shape we use elsewhere.
_SCAN_ID_RE = re.compile(r"scan_id=([0-9a-fA-F-]{32,})")


class PhaseEnforcerMiddleware(AgentMiddleware):
    """Force the orchestrator to complete each phase before terminating."""

    name: str = "rr_phase_enforcer"

    def __init__(self) -> None:
        super().__init__()
        self._corrections_per_thread: dict[str, int] = {}

    # ----- internal helpers -----------------------------------------------

    @staticmethod
    def _scan_id_from_state(state: dict[str, Any]) -> str | None:
        """Find scan_id in the most recent HumanMessage."""
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
    def _next_missing_phase(scan_id: str) -> str | None:
        """Return the NAME of the first incomplete phase, or None if
        every phase has its fs artifact in place."""
        # Discovery: at least one source file present
        if not fs_list(scan_id, prefix="discovery/"):
            return "discovery"
        # Triage: top_n.json written
        if fs_read(scan_id, FS_FILE_TRIAGE_TOPN) is None:
            return "triage"
        # Deep_read: ≥1 extraction (the orchestrator should fan-out N but
        # 1+ proves the phase actually ran)
        if not fs_list(scan_id, prefix="extractions/"):
            return "deep_read"
        # Synthesis: report.json
        if fs_read(scan_id, FS_FILE_SYNTHESIS_REPORT) is None:
            return "synthesis"
        # All done (digest comes from Python post-agent; nothing to enforce)
        return None

    # ----- middleware hooks -----------------------------------------------

    def after_model(self, state: dict[str, Any]) -> dict[str, Any] | None:
        """Called after each model output. If the agent is trying to end
        without all phases done, inject a corrective HumanMessage so it
        runs another loop instead of terminating."""
        messages = state.get("messages", []) or []
        if not messages:
            return None
        last = messages[-1]
        # If the last AIMessage emitted tool_calls, the agent will run them
        # — don't interfere.
        if getattr(last, "tool_calls", None):
            return None
        # Only police AIMessages (the model's "I'm done" signal).
        if type(last).__name__ != "AIMessage":
            return None

        scan_id = self._scan_id_from_state(state)
        if not scan_id:
            return None

        # Cap corrections so we never loop forever.
        n = self._corrections_per_thread.get(scan_id, 0)
        if n >= MAX_CORRECTIONS:
            logger.warning(
                f"[phase-enforcer] scan_id={scan_id} reached MAX_CORRECTIONS "
                f"({MAX_CORRECTIONS}); allowing agent to end"
            )
            return None

        missing = self._next_missing_phase(scan_id)
        if missing is None:
            return None  # everything done; allow termination

        nudge_map = {
            "discovery":  "You have not yet called all 4 discovery tools (discover_arxiv, discover_semantic_scholar, discover_huggingface_daily_papers, discover_hn). Call any that haven't run yet, in PARALLEL in ONE message.",
            "triage":     f"Discovery is done but you have not called triage_candidates(scan_id='{scan_id}', profile_verticals=[...], top_n=N). Call it NOW — it is unconditional even if some discoveries returned 0.",
            "deep_read":  f"Triage is done. You have not dispatched deep_read for the papers in fs/triage/top_n.json. Use task(subagent_type='deep_read', description='scan_id={scan_id} arxiv_id=<id>') — one call per paper, ALL IN ONE MESSAGE for parallel execution.",
            "synthesis":  f"Deep_read is done. Dispatch task(subagent_type='synthesis', description='scan_id={scan_id}') NOW.",
        }
        nudge = nudge_map[missing]

        self._corrections_per_thread[scan_id] = n + 1
        logger.info(
            f"[phase-enforcer] scan_id={scan_id} next_missing={missing!r} "
            f"injecting corrective message (#{n + 1}/{MAX_CORRECTIONS})"
        )
        # Append a HumanMessage so the next model turn sees a fresh "you
        # must do X" instruction without erasing the history.
        return {"messages": [HumanMessage(content=nudge)]}
