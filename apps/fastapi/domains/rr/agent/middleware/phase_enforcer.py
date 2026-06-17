"""PhaseEnforcerMiddleware — keeps the orchestrator running until phases complete.

The orchestrator's LLM often emits a few discovery tool_calls and then
returns a "looks good" summary message without calling triage_candidates /
deep_read / etc. DeepAgents accepts that as termination, the agent exits,
and we end up at the `_build_digest_from_fs` fallback with no extractions.

2026-06-15 REWRITE: switched from `after_model` to `before_model`. Scan
fd48309a showed `after_model`'s returned HumanMessage NEVER re-entered
the agent loop — the AIMessage with no tool_calls was treated as terminal
regardless of middleware's return value. `before_model` runs at the
START of every model turn, so an injected SystemMessage actually reaches
the LLM's decision context. We also bump corrections cap back to 6 since
each correction is now active not advisory.

Strategy: ALWAYS check fs state before each model turn. When phases are
incomplete, prepend a high-priority SystemMessage that EXPLAINS what's
missing AND what tool/subagent to call next. The LLM sees this fresh on
every turn until the phase is complete.

This middleware inspects the per-scan fs state before each model call.
When fs is incomplete (e.g. discoveries done but triage NOT done), it
injects a SystemMessage saying "you haven't called X yet — call it now"
so the LLM's next decision sees the constraint.

Failure modes if used incorrectly:
  - Forces the orchestrator into an infinite loop if the corrective
    message never lands. Cap at MAX_CORRECTIONS injections (default 6).
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

from langchain_core.messages import SystemMessage

from ..keys import (
    FS_DISCOVERY_KEY_ARXIV,
    FS_DISCOVERY_KEY_HF,
    FS_DISCOVERY_KEY_HN,
    FS_DISCOVERY_KEY_S2,
    FS_FILE_DIGEST,
    FS_FILE_SYNTHESIS_REPORT,
    FS_FILE_TRIAGE_TOPN,
)
from ..tools.state import fs_list, fs_read


# All 4 discovery files we expect to see before the discovery phase is
# considered complete. Each maps to a discover_* subagent. Every subagent's
# `_DISCOVERY_TAIL` prompt mandates `stash_discovery_result` even when
# the upstream MCP tool returned zero papers (an empty list is the
# correct empty-result signal), so absence of one of these keys is a
# real signal that the corresponding subagent didn't run.
_REQUIRED_DISCOVERY_KEYS: tuple[str, ...] = (
    FS_DISCOVERY_KEY_ARXIV,
    FS_DISCOVERY_KEY_S2,
    FS_DISCOVERY_KEY_HF,
    FS_DISCOVERY_KEY_HN,
)


logger = logging.getLogger(__name__)


# Each injection = one extra LLM turn's context. Cap was 6 (2026-06-15);
# bumped to 15 (2026-06-16) after scan 307f28ad showed synthesis never
# got a nudge — discovery alone burned 3 nudges (the orchestrator was
# slow to fan out all 4 discoveries in one batch), triage took 2,
# deep_read got the 6th and last, and synthesis never ran. 15 gives
# every phase at least 2-3 nudges of headroom without being a runaway.
# The safety net is still real: if the agent legitimately can't make
# progress after MAX nudges, `_build_digest_from_fs` builds a degraded
# digest from whatever fs has — AND `task.py`'s post-agent missing-
# extractions backfill (2026-06-16) fires inline deep_reads for any
# top_n arxiv_id that the orchestrator dropped, recovering most
# previously-degraded scans without re-running the agent.
#
# 2026-06-16: bumped 15 → 20 after scan fd9ad127 exhausted 15/15 with
# 7/8 deep_reads done. The extra 5 nudges give the orchestrator more
# room to recover one stuck subagent before synthesis fires; combined
# with the backfill safety net, the degradation rate should drop to
# near-zero for top_n ≤ 12 scans.
MAX_CORRECTIONS = 20


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
    def _missing_discovery_sources(scan_id: str) -> list[str]:
        """Return the list of source names whose discovery file is still
        missing. Empty list ⇒ all 4 subagents have stashed (possibly 0)."""
        missing: list[str] = []
        for key in _REQUIRED_DISCOVERY_KEYS:
            if fs_read(scan_id, key) is None:
                # key path is e.g. 'discovery/arxiv.json' → source name 'arxiv'
                source = key.split("/", 1)[1].rsplit(".", 1)[0]
                missing.append(source)
        return missing

    @staticmethod
    def _missing_deep_read_arxiv_ids(scan_id: str) -> list[str]:
        """Return the list of arxiv_ids from triage's top_n.json that
        still LACK an extraction file. Empty list ⇒ all top_n papers
        have been deep_read.

        2026-06-15: needed after scan 77f47013 showed the orchestrator
        dispatching synthesis after only 2 of 4 deep_read subagents,
        producing `partial_extractions_2_of_4`. Mirrors the same fix
        pattern as `_missing_discovery_sources`.
        """
        top_n = fs_read(scan_id, FS_FILE_TRIAGE_TOPN)
        if not isinstance(top_n, list) or not top_n:
            return []  # triage hasn't run; deep_read can't be evaluated
        expected_ids = [
            str(p.get("arxiv_id") or "") for p in top_n
            if isinstance(p, dict) and p.get("arxiv_id")
        ]
        if not expected_ids:
            return []  # no arxiv_ids in top_n; nothing to deep_read
        existing = fs_list(scan_id, prefix="extractions/")
        # Extraction filenames are `extractions/<arxiv_id>.json`. Strip
        # both the prefix and the `.json` suffix to compare ids.
        present_ids = set()
        for path in existing:
            if path.startswith("extractions/") and path.endswith(".json"):
                aid = path[len("extractions/"):-len(".json")]
                if aid:
                    present_ids.add(aid)
        return [aid for aid in expected_ids if aid not in present_ids]

    @classmethod
    def _next_missing_phase(cls, scan_id: str) -> str | None:
        """Return the NAME of the first incomplete phase, or None if
        every phase has its fs artifact in place.

        2026-06-15 fix: discovery gate now requires ALL 4 source files
        (not just ≥1). Scan 4ac0e187 surfaced the previous-version bug
        where arxiv stashing alone closed the discovery gate, letting
        the orchestrator skip HF / HN / S2 subagents entirely. Each
        subagent's `_DISCOVERY_TAIL` prompt requires `stash_discovery_result`
        even with count=0 (an empty-list stash is the correct empty-
        result signal), so an absent file means the subagent didn't run.
        """
        # Discovery: ALL 4 source files present (count=0 is OK; absence is not)
        if cls._missing_discovery_sources(scan_id):
            return "discovery"
        # Triage: top_n.json written
        if fs_read(scan_id, FS_FILE_TRIAGE_TOPN) is None:
            return "triage"
        # Deep_read: extraction file for EACH arxiv_id in top_n.json
        # (was `≥1 extraction` — scan 77f47013 surfaced the bug where
        # the orchestrator dispatched synthesis after only 2/4 deep_reads
        # because the gate closed at the first extraction).
        if cls._missing_deep_read_arxiv_ids(scan_id):
            return "deep_read"
        # Synthesis: report.json
        if fs_read(scan_id, FS_FILE_SYNTHESIS_REPORT) is None:
            return "synthesis"
        # All done (digest comes from Python post-agent; nothing to enforce)
        return None

    @staticmethod
    def _last_was_terminal(messages: list[Any]) -> bool:
        """Heuristic: the agent is trying to terminate when the last
        message is an AIMessage with NO tool_calls. We use this to ALSO
        inject the nudge when termination looks imminent — the
        before_model hook normally runs at the START of every turn, but
        when the agent's last AIMessage was a terminal one, that turn
        won't happen unless we proactively re-enter via the injected
        constraint message."""
        if not messages:
            return False
        last = messages[-1]
        if type(last).__name__ != "AIMessage":
            return False
        return not getattr(last, "tool_calls", None)

    # ----- middleware hooks -----------------------------------------------

    def before_model(self, state: dict[str, Any], runtime: Any = None) -> dict[str, Any] | None:
        """Run before every model turn. If the scan's fs shows an incomplete
        phase, prepend a high-priority SystemMessage to the conversation
        history so the LLM's next decision sees the constraint freshly.

        This replaces the previous `after_model` injection — which scan
        fd48309a proved was being silently ignored when the LLM's last
        AIMessage was terminal (no tool_calls). before_model runs at the
        START of every model call, so the injected message ALWAYS reaches
        the LLM's context window before its next decision.
        """
        messages = state.get("messages", []) or []
        if not messages:
            return None

        scan_id = self._scan_id_from_state(state)
        if not scan_id:
            return None

        # Cap injections so a broken loop doesn't burn the rate budget.
        n = self._corrections_per_thread.get(scan_id, 0)
        if n >= MAX_CORRECTIONS:
            return None

        missing = self._next_missing_phase(scan_id)
        if missing is None:
            return None  # everything done; allow the agent to terminate

        # Don't re-inject if the last message in history is ALREADY a
        # phase-enforcer SystemMessage we appended — that means the LLM
        # just saw the nudge on the previous turn and didn't act on it
        # yet. Re-stuffing the context noisily before the model has had
        # a chance to respond wastes tokens and confuses the conversation.
        last = messages[-1]
        last_content = getattr(last, "content", "") or ""
        if (
            type(last).__name__ == "SystemMessage"
            and isinstance(last_content, str)
            and last_content.startswith("[phase-enforcer]")
        ):
            return None

        if missing == "discovery":
            # Build the nudge dynamically so the LLM sees the EXACT list
            # of sources whose stash file is still absent. Generic "all 4"
            # nudges let the agent move on after one stash (scan 4ac0e187).
            missing_sources = self._missing_discovery_sources(scan_id)
            subagent_map = {
                "arxiv":                     "discovery_arxiv",
                "semantic_scholar":          "discovery_semantic_scholar",
                "huggingface_daily_papers":  "discovery_huggingface_daily_papers",
                "hn":                        "discovery_hn",
            }
            calls = [
                f"task(subagent_type='{subagent_map[s]}', "
                f"description=\"scan_id={scan_id} topic='<topic>' verticals=<list>\")"
                for s in missing_sources if s in subagent_map
            ]
            nudge_body = (
                f"Discovery is INCOMPLETE — the following source files are still "
                f"missing from fs: {missing_sources!r}. The orchestrator MUST "
                f"dispatch all 4 discovery subagents (or all 4 discover_* tools "
                f"in tools mode), not just one. Each subagent's stash_discovery_result "
                f"creates discovery/<source>.json — an empty list is the correct "
                f"empty-result signal but the file MUST exist. Dispatch the missing "
                f"subagents now, IN ONE MESSAGE for parallel execution:\n"
                + "\n".join(calls)
            )
        elif missing == "deep_read":
            # Name the EXACT arxiv_ids whose extraction is still missing.
            # Generic "dispatch deep_read for the top_n papers" let the
            # orchestrator move on after 2/4 in scan 77f47013.
            missing_ids = self._missing_deep_read_arxiv_ids(scan_id)
            calls = [
                f"task(subagent_type='deep_read', "
                f"description=\"scan_id={scan_id} arxiv_id='{aid}'\")"
                for aid in missing_ids
            ]
            nudge_body = (
                f"Deep_read is INCOMPLETE — the following arxiv_ids from "
                f"fs/triage/top_n.json still lack an extraction file: "
                f"{missing_ids!r}. Dispatch one deep_read task PER missing "
                f"arxiv_id, ALL IN ONE MESSAGE for parallel execution. "
                f"DO NOT skip to synthesis until every top_n paper has an "
                f"extraction on disk (the ScanComplete validator will "
                f"reject a terminal output with missing extractions).\n"
                + "\n".join(calls)
            )
        else:
            nudge_map = {
                "triage":     f"Discovery is done but you have not called triage_candidates(scan_id='{scan_id}', topic='<topic>', profile_verticals=[...], top_n=N). Call it NOW — it is unconditional even if some discoveries returned 0.",
                "synthesis":  f"Deep_read is done. Dispatch task(subagent_type='synthesis', description='scan_id={scan_id}') NOW — followed by task(subagent_type='report', description='scan_id={scan_id}'). Both subagents MUST run before you emit respond_in_format. The ScanComplete Pydantic validator will REJECT your terminal output if synthesis or report are missing from phases.",
            }
            nudge_body = nudge_map[missing]
        # Tag the message so we can detect our own injection and avoid
        # double-stacking. SystemMessage role is high-priority and survives
        # the agent's message-pruning logic.
        nudge = SystemMessage(
            content=f"[phase-enforcer] next_missing={missing}: {nudge_body}"
        )

        self._corrections_per_thread[scan_id] = n + 1
        logger.info(
            f"[phase-enforcer] scan_id={scan_id} next_missing={missing!r} "
            f"injecting BEFORE-model nudge (#{n + 1}/{MAX_CORRECTIONS})"
        )
        return {"messages": [nudge]}
