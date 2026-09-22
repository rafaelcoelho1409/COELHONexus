"""Custom DeepAgents middleware for the RR agent — Imperative Shell.

Two pieces of cross-cutting behavior that don't fit any single subagent
or tool, merged here per docs/CODE-CONVENTIONS.md §8 strict-merge (both
are `AgentMiddleware` subclasses — same role):

  PhaseEnforcerMiddleware  Prevents the orchestrator from terminating
                           while phases are still incomplete (before_model
                           nudges), AND hard-blocks a handful of confirmed-
                           unwanted tool calls via awrap_tool_call —
                           nudges alone can't stop a voluntary action the
                           LLM decides to take despite being told not to.
  PhaseEventsMiddleware    Emits Redis pub/sub events at every model
                           call so the SSE stream has per-phase
                           granularity instead of just the Celery task
                           boundaries.

Both subclass `langchain.agents.middleware.AgentMiddleware`. Wired into
`create_deep_agent(middleware=[...])` in ../graph.py.
"""
from __future__ import annotations
import infra
from .. import keys, params, patterns, prompts, tools
from ...runtime import service as runtime_service

import logging
import time
from typing import Any

from opentelemetry import context as _otel_ctx
try:
    from langchain.agents.middleware import AgentMiddleware
except ImportError:                                                       # pragma: no cover
    from langchain.agents.middleware.types import AgentMiddleware         # type: ignore
from langchain_core.messages import SystemMessage, ToolMessage


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# PhaseEnforcerMiddleware — injects before_model SystemMessages to keep the
# orchestrator running until every phase has its fs artifact on disk.
#
# Uses before_model (not after_model) so the injected message reaches the
# LLM's decision context before its next call — after_model's returned
# HumanMessage was silently ignored when the AIMessage was terminal (no
# tool_calls).
# ---------------------------------------------------------------------------

class PhaseEnforcerMiddleware(AgentMiddleware):
    """Force the orchestrator to complete each phase before terminating."""

    name: str = "rr_phase_enforcer"

    # 3+ consecutive nudges for the SAME phase means plain instructions
    # aren't landing — escalate the wording rather than repeat it verbatim.
    STUCK_STREAK_THRESHOLD = 3

    def __init__(self) -> None:
        super().__init__()
        self._corrections_per_thread: dict[str, int] = {}
        # scan_id -> (last next_missing phase, consecutive nudge count for it)
        self._same_phase_streak: dict[str, tuple[str, int]] = {}

    @staticmethod
    def _scan_id_from_state(state: dict[str, Any]) -> str | None:
        messages = state.get("messages", []) or []
        for m in messages:
            content = getattr(m, "content", "") or ""
            if not isinstance(content, str):
                continue
            m2 = patterns.SCAN_ID_RE.search(content)
            if m2:
                return m2.group(1)
        return None

    @staticmethod
    def _missing_discovery_sources(scan_id: str) -> list[str]:
        """Return source names whose discovery file is still absent. Empty ⇒ all 5 subagents stashed."""
        missing: list[str] = []
        for key in keys.REQUIRED_DISCOVERY_KEYS:
            if tools.state.fs_read(scan_id, key) is None:
                source = key.split("/", 1)[1].rsplit(".", 1)[0]
                missing.append(source)
        return missing

    @staticmethod
    def _missing_deep_read_arxiv_ids(scan_id: str) -> list[str]:
        """Return arxiv_ids from triage's top_n that still lack an extraction file."""
        top_n = tools.state.fs_read(scan_id, keys.FS_FILE_TRIAGE_TOPN)
        if not isinstance(top_n, list) or not top_n:
            return []
        expected_ids = [
            str(p.get("arxiv_id") or "") for p in top_n
            if isinstance(p, dict) and p.get("arxiv_id")
        ]
        if not expected_ids:
            return []
        existing = tools.state.fs_list(scan_id, prefix="extractions/")
        present_ids = set()
        for path in existing:
            if path.startswith("extractions/") and path.endswith(".json"):
                aid = path[len("extractions/"):-len(".json")]
                if aid:
                    present_ids.add(aid)
        return [aid for aid in expected_ids if aid not in present_ids]

    @classmethod
    def _next_missing_phase(cls, scan_id: str) -> str | None:
        """Return the name of the first incomplete phase, or None when all artifacts exist."""
        # Discovery: ALL 5 source files required (count=0 is OK; absence is not).
        if cls._missing_discovery_sources(scan_id):
            return "discovery"
        top_n = tools.state.fs_read(scan_id, keys.FS_FILE_TRIAGE_TOPN)
        if top_n is None:
            return "triage"
        # A zero-candidate run is a legitimate, complete outcome — triage
        # always writes `[]` rather than leaving top_n.json missing (see
        # triage/service.py). Nothing to deep_read or synthesize.
        #
        # This is its OWN phase (not None) so `before_model` keeps actively
        # telling the orchestrator to wrap up instead of going silent —
        # silence reads as "nothing required" to the LLM, not "you're
        # done, stop trying things." Observed live (scan b4643c7b,
        # 2026-09-20): with this returning None, the orchestrator still
        # called graph_build_papers 3x and dispatched synthesis twice on
        # an empty top_n, burning ~30 min for zero benefit.
        if isinstance(top_n, list) and not top_n:
            return "finalize_empty"
        # All top_n papers must have extraction files, not just ≥1.
        if cls._missing_deep_read_arxiv_ids(scan_id):
            return "deep_read"
        if tools.state.fs_read(scan_id, keys.FS_FILE_SYNTHESIS_REPORT) is None:
            return "synthesis"
        return None

    @staticmethod
    def _is_zero_candidate(scan_id: str) -> bool:
        """True once triage has confirmed 0 candidates (top_n.json == [],
        not merely absent). Shared by `_next_missing_phase` (nudge) and
        `awrap_tool_call` (hard block) so both read the same signal."""
        top_n = tools.state.fs_read(scan_id, keys.FS_FILE_TRIAGE_TOPN)
        return isinstance(top_n, list) and not top_n

    @staticmethod
    def _scan_id_from_description(description: Any) -> str | None:
        """Extract scan_id from a task() call's `description` arg — the
        only place it appears in a subagent dispatch (the tool has no
        `scan_id` kwarg of its own)."""
        if not isinstance(description, str):
            return None
        m = patterns.SCAN_ID_RE.search(description)
        return m.group(1) if m else None

    @staticmethod
    def _block(request: Any, tool_label: str, reason: str) -> ToolMessage:
        logger.warning(f"[phase-enforcer] BLOCKED {tool_label} — {reason}")
        return ToolMessage(
            tool_call_id = request.tool_call["id"],
            content      = f"BLOCKED: {reason}",
        )

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        """Hard-block a handful of confirmed-unwanted tool calls instead
        of relying on the orchestrator to read and obey prompt text.

        Prompt nudges (before_model, above) only cover REQUIRED actions —
        they have no way to stop a voluntary, unwanted one. Observed live
        (scan b4643c7b, 2026-09-20): despite explicit "do NOT call this"
        instructions, the orchestrator called graph_build_papers 3x and
        dispatched a synthesis subagent twice on a confirmed-empty
        top_n.json, burning ~30 minutes for zero benefit. A deterministic
        Python check here can't be argued with the way a system prompt
        can be ignored.

        A second incident (scan 36c7877e, 2026-09-20) showed the SAME
        redispatch pattern on a NON-empty result: synthesis wrote a valid
        report once, then got dispatched and wrote an identical report
        again ~15 minutes later. The zero-candidate checks above don't
        catch this — added explicit "already has a result" checks for
        synthesis and graph_build below, mirroring the discovery-source
        redispatch guard's logic.
        """
        name = request.tool_call.get("name")
        args = request.tool_call.get("args") or {}

        if name == keys.TOOL_GRAPH_BUILD:
            scan_id = args.get("scan_id") or self._scan_id_from_state(request.state)
            if scan_id and self._is_zero_candidate(scan_id):
                return self._block(
                    request, keys.TOOL_GRAPH_BUILD,
                    "0 candidates confirmed for this scan — nothing to "
                    "build. Emit your final ScanComplete response now.",
                )
            if scan_id and tools.state.fs_read(scan_id, keys.FS_FILE_GRAPH_BUILD_DONE) is not None:
                return self._block(
                    request, keys.TOOL_GRAPH_BUILD,
                    "graph_build already ran for this scan — do not call "
                    "it again. Proceed to synthesis or your final "
                    "ScanComplete response.",
                )

        elif name == "task":
            subagent_type = args.get("subagent_type")
            scan_id = (
                self._scan_id_from_description(args.get("description"))
                or self._scan_id_from_state(request.state)
            )
            if scan_id:
                if subagent_type == keys.SUBAGENT_SYNTHESIS and self._is_zero_candidate(scan_id):
                    return self._block(
                        request, f"task(subagent_type={subagent_type!r})",
                        "0 candidates confirmed for this scan — nothing "
                        "to synthesize. Emit your final ScanComplete "
                        "response now.",
                    )
                if (
                    subagent_type == keys.SUBAGENT_SYNTHESIS
                    and tools.state.fs_read(scan_id, keys.FS_FILE_SYNTHESIS_REPORT) is not None
                ):
                    return self._block(
                        request, f"task(subagent_type={subagent_type!r})",
                        "synthesis already wrote a report for this scan — "
                        "do not dispatch it again. Emit your final "
                        "ScanComplete response now.",
                    )
                discovery_key = keys.SUBAGENT_TO_DISCOVERY_KEY.get(subagent_type)
                if discovery_key and tools.state.fs_read(scan_id, discovery_key) is not None:
                    return self._block(
                        request, f"task(subagent_type={subagent_type!r})",
                        "this discovery source already has a result "
                        "(even an empty one) for this scan — do not "
                        "redispatch it.",
                    )

        return await handler(request)

    @staticmethod
    def _last_was_terminal(messages: list[Any]) -> bool:
        if not messages:
            return False
        last = messages[-1]
        if type(last).__name__ != "AIMessage":
            return False
        return not getattr(last, "tool_calls", None)

    def before_model(self, state: dict[str, Any], runtime: Any = None) -> dict[str, Any] | None:
        """Inject a high-priority SystemMessage when a phase is incomplete."""
        messages = state.get("messages", []) or []
        if not messages:
            return None

        scan_id = self._scan_id_from_state(state)
        if not scan_id:
            return None

        n = self._corrections_per_thread.get(scan_id, 0)
        if n >= params.PARAMS.max_phase_corrections:
            return None

        missing = self._next_missing_phase(scan_id)
        if missing is None:
            return None

        last = messages[-1]
        last_content = getattr(last, "content", "") or ""
        if (
            type(last).__name__ == "SystemMessage"
            and isinstance(last_content, str)
            and last_content.startswith("[phase-enforcer]")
        ):
            return None

        if missing == "discovery":
            missing_sources = self._missing_discovery_sources(scan_id)
            calls = [
                f"task(subagent_type='{keys.DISCOVERY_SOURCE_TO_SUBAGENT[s]}', "
                f"description=\"scan_id={scan_id} topic='<topic>' verticals=<list>\")"
                for s in missing_sources if s in keys.DISCOVERY_SOURCE_TO_SUBAGENT
            ]
            nudge_body = prompts.PHASE_ENFORCER_DISCOVERY_NUDGE.format(
                missing_sources = missing_sources,
                calls           = "\n".join(calls),
            )
        elif missing == "deep_read":
            missing_ids = self._missing_deep_read_arxiv_ids(scan_id)
            calls = [
                f"task(subagent_type='deep_read', "
                f"description=\"scan_id={scan_id} arxiv_id='{aid}'\")"
                for aid in missing_ids
            ]
            nudge_body = prompts.PHASE_ENFORCER_DEEP_READ_NUDGE.format(
                missing_ids = missing_ids,
                calls       = "\n".join(calls),
            )
        else:
            nudge_map = {
                "triage":         prompts.PHASE_ENFORCER_TRIAGE_NUDGE.format(scan_id=scan_id),
                "synthesis":      prompts.PHASE_ENFORCER_SYNTHESIS_NUDGE.format(scan_id=scan_id),
                "finalize_empty": prompts.PHASE_ENFORCER_FINALIZE_EMPTY_NUDGE,
            }
            nudge_body = nudge_map[missing]

        prev_phase, streak = self._same_phase_streak.get(scan_id, (None, 0))
        streak = streak + 1 if missing == prev_phase else 1
        self._same_phase_streak[scan_id] = (missing, streak)
        if streak >= self.STUCK_STREAK_THRESHOLD:
            nudge_body = prompts.PHASE_ENFORCER_ESCALATION_PREFIX.format(streak=streak) + nudge_body
            logger.warning(
                f"[phase-enforcer] scan_id={scan_id} STUCK on "
                f"next_missing={missing!r} for {streak} consecutive nudges "
                f"— orchestrator not complying, escalating nudge wording"
            )

        nudge = SystemMessage(
            content=f"[phase-enforcer] next_missing={missing}: {nudge_body}"
        )

        self._corrections_per_thread[scan_id] = n + 1
        logger.info(
            f"[phase-enforcer] scan_id={scan_id} next_missing={missing!r} "
            f"injecting BEFORE-model nudge (#{n + 1}/{params.PARAMS.max_phase_corrections})"
        )
        return {"messages": [nudge]}


# ---------------------------------------------------------------------------
# PhaseEventsMiddleware — emits per-phase SSE events to Redis pub/sub + OTel
# spans.
#
# Today the Celery task emits 3 events per scan: running / persisting / done.
# The SSE stream goes idle during the 1-5 min the agent is running. This
# middleware hooks every model call + tool dispatch and emits granular
# events so the UI status strip can show:
#
#   Phase: discovery (5/5 sources stashed)
#   Phase: triage   (66 candidates → top 8)
#   Phase: deep_read (3/8 extractions written)
#   Phase: synthesis (writing themes...)
#
# OTel spans: on each phase transition, a completed `rr.node.{phase}` span
# is emitted with the correct start/end timestamps so LangFuse shows each
# pipeline stage as a timed child of rr.scan.run. Call finalize_scan() after
# agent.ainvoke to close the final phase span.
#
# Implementation is best-effort: a Redis hiccup or OTel failure logs but does
# NOT abort the agent run. emit_event_sync is already used by task.py for the
# same channel so the SSE relay code on the FastAPI side needs no change.
# ---------------------------------------------------------------------------

class PhaseEventsMiddleware(AgentMiddleware):
    """Emit a Redis pub/sub event + OTel span per pipeline phase."""

    name: str = "rr_phase_events"

    def __init__(self) -> None:
        super().__init__()
        # Per-scan last (phase, message) pair emitted. Dedup must include the
        # message — within the discovery phase the message ticks 0/5 → 5/5,
        # and the UI relies on those granular updates. Dedup by phase alone
        # would swallow them and the strip would freeze on "0/5 sources stashed"
        # until phase=triage fires.
        self._last_emit: dict[str, tuple[str, str]] = {}
        # (scan_id) → (phase_name, start_ns, otel_context_snapshot)
        self._phase_timing: dict[str, tuple[str, int, Any]] = {}

    @staticmethod
    def _scan_id_from_state(state: dict[str, Any]) -> str | None:
        messages = state.get("messages", []) or []
        for m in messages:
            content = getattr(m, "content", "") or ""
            if not isinstance(content, str):
                continue
            m2 = patterns.SCAN_ID_RE.search(content)
            if m2:
                return m2.group(1)
        return None

    @staticmethod
    def _current_phase(scan_id: str) -> tuple[str, str]:
        """(phase_name, message) — what the agent is doing RIGHT NOW based
        on what's in fs. Same precedence as the phase enforcer."""
        n_discoveries = len(tools.state.fs_list(scan_id, prefix="discovery/"))
        n_required = len(keys.REQUIRED_DISCOVERY_KEYS)
        if n_discoveries < n_required:
            return "discovery", f"{n_discoveries}/{n_required} sources stashed"
        topn_raw = tools.state.fs_read(scan_id, keys.FS_FILE_TRIAGE_TOPN)
        if topn_raw is None:
            return "triage", "ranking + dedup"
        if isinstance(topn_raw, list) and not topn_raw:
            return "finalizing", "0 candidates matched — nothing to deep_read or synthesize"
        n_extractions = len(tools.state.fs_list(scan_id, prefix="extractions/"))
        topn = topn_raw or []
        if n_extractions < len(topn):
            return "deep_read", f"{n_extractions}/{len(topn)} extractions written"
        if tools.state.fs_read(scan_id, keys.FS_FILE_SYNTHESIS_REPORT) is None:
            return "synthesis", "clustering themes"
        # 2026-09-17: was `return "done", ...` — collided with the REAL
        # terminal "done" event `task.py` emits after digest assembly +
        # Postgres persist complete. This fires as soon as the synthesis
        # report exists on fs and the orchestrator's next own turn
        # returns — but the orchestrator still needs 1-2 more turns to
        # produce its final ScanComplete structured output before
        # Python-side digest assembly even starts. Confirmed live: a
        # scan showed "Done" in the UI ~2 minutes before the Postgres
        # row actually flipped to status='done'. Frontend code treating
        # `phase === "done"` as terminal (main.js) couldn't tell these
        # two events apart because they carried the identical string.
        return "finalizing", "agent finished — task is persisting"

    @staticmethod
    def _emit_phase_span(
        scan_id: str, phase: str, start_ns: int, end_ns: int, ctx: Any
    ) -> None:
        try:
            span = infra.otel.service.get_tracer().start_span(
                f"rr.node.{phase}",
                context=ctx,
                start_time=start_ns,
                attributes={
                    "coelho.langfuse.keep": True,
                    "rr.scan_id": scan_id,
                    "rr.phase": phase,
                    "rr.phase_duration_ms": round((end_ns - start_ns) / 1_000_000),
                },
            )
            span.end(end_time=end_ns)
        except Exception as exc:
            logger.debug(f"[rr-phase-events] OTel span emit failed: {exc}")

    def _transition_phase(self, scan_id: str, new_phase: str) -> None:
        now_ns = time.time_ns()
        ctx = _otel_ctx.get_current()
        existing = self._phase_timing.pop(scan_id, None)
        if existing:
            prev_phase, start_ns, prev_ctx = existing
            self._emit_phase_span(scan_id, prev_phase, start_ns, now_ns, prev_ctx)
        self._phase_timing[scan_id] = (new_phase, now_ns, ctx)

    def finalize_scan(self, scan_id: str) -> None:
        """End the last open phase span — call after agent.ainvoke completes."""
        existing = self._phase_timing.pop(scan_id, None)
        if existing:
            prev_phase, start_ns, ctx = existing
            self._emit_phase_span(scan_id, prev_phase, start_ns, time.time_ns(), ctx)

    def after_model(self, state: dict[str, Any]) -> dict[str, Any] | None:
        scan_id = self._scan_id_from_state(state)
        if not scan_id:
            return None
        phase, message = self._current_phase(scan_id)

        existing = self._phase_timing.get(scan_id)
        if not existing or existing[0] != phase:
            self._transition_phase(scan_id, phase)

        if self._last_emit.get(scan_id) == (phase, message):
            return None
        self._last_emit[scan_id] = (phase, message)
        # Best-effort — emit_event_sync is sync + catches its own errors.
        runtime_service.emit_event_sync(scan_id, phase, message=message)
        return None
