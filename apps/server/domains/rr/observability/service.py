"""RR observability — metric recorders, the shared `@traced_tool` span decorator and the scan-read workflow span.

`traced_tool` is the `execute_tool` span decorator for every `@tool` function under
`domains.rr.agent.tools.*` — OTel GenAI semantic conventions (Development stability):
https://github.com/open-telemetry/semantic-conventions-genai
(`docs/gen-ai/gen-ai-spans.md`, "Execute tool span"). Apply it directly under `@tool`
(`@tool` must stay the OUTERMOST decorator — it introspects the function's
signature/docstring to build the tool schema; `functools.wraps` preserves
`__name__`/`__doc__`/`__annotations__`/`__wrapped__` so that introspection still sees
the real function, not this wrapper's `*args, **kwargs`):

    @tool
    @traced_tool
    async def discover_arxiv(query: str) -> str:
        ...

The store-layer client spans (`db.*` / `aws.s3.*`) come from the infra modules
(`infra.postgres.service.span`, `infra.neo4j.service.span`, `infra.qdrant.service.span`,
`infra.minio.service.span`). Phase-transition spans stay in
`agent/middleware/service.py` — tightly coupled to `PhaseEventsMiddleware`'s own state.
"""
from __future__ import annotations
import infra

import asyncio
import contextlib
import functools
import uuid
from collections.abc import Iterator


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def record_scan_run(
    *,
    degraded: bool,
    outcome: str,
    duration_s: float | None = None,
    findings: int | None = None,
    candidates: int | None = None,
    theme_count: int | None = None,
) -> None:
    attrs = {
        "outcome":  outcome or "unknown",
        "degraded": str(bool(degraded)).lower(),
    }
    try:
        if (inst := infra.opentelemetry.service.get_instrument("rr_scan_run_total")) is not None:
            inst.add(1, attributes = attrs)
        if duration_s is not None and (
            inst := infra.opentelemetry.service.get_instrument("rr_scan_run_duration")
        ) is not None:
            inst.record(max(duration_s, 0.0), attributes = attrs)
        if findings is not None and (inst := infra.opentelemetry.service.get_instrument("rr_findings")) is not None:
            inst.record(max(findings, 0), attributes = attrs)
        if candidates is not None and (
            inst := infra.opentelemetry.service.get_instrument("rr_candidates")
        ) is not None:
            inst.record(max(candidates, 0), attributes = attrs)
        if theme_count is not None and (
            inst := infra.opentelemetry.service.get_instrument("rr_theme_count")
        ) is not None:
            inst.record(max(theme_count, 0), attributes = attrs)
    except Exception:
        pass


def record_phase_event(*, phase: str) -> None:
    try:
        if (inst := infra.opentelemetry.service.get_instrument("rr_phase_event_total")) is not None:
            inst.add(1, attributes = {"phase": phase or "unknown"})
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Spans
# ---------------------------------------------------------------------------

def _tool_span_attrs(tool_name: str) -> dict:
    return {
        "gen_ai.operation.name": "execute_tool",
        "gen_ai.tool.name":      tool_name,
        "gen_ai.tool.type":      "function",
        "gen_ai.agent.name":     "rr-orchestrator",
        infra.langfuse.keys.KEEP:  True,
    }


def traced_tool(fn):
    """Wrap a LangChain `@tool` function (sync or async) in an `execute_tool {name}` span."""
    tool_name = fn.__name__

    def _span():
        return infra.opentelemetry.service.client_span(
            f"execute_tool {tool_name}",
            _tool_span_attrs(tool_name),
            kind = infra.opentelemetry.service.SpanKind.INTERNAL,
        )

    if asyncio.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def _async_wrapper(*args, **kwargs):
            with _span():
                return await fn(*args, **kwargs)
        return _async_wrapper

    @functools.wraps(fn)
    def _sync_wrapper(*args, **kwargs):
        with _span():
            return fn(*args, **kwargs)
    return _sync_wrapper


@contextlib.contextmanager
def scan_read_span(operation: str, *, scan_id: str) -> Iterator[None]:
    """Workflow-root span + LangFuse session for the scan READ/viewing
    endpoints (`GET /scan/{id}`, `/llm-counters`, `/fs/*`, `/events`
    SSE, `/scans/recent`, `/finding/{arxiv_id}/code`) — 2026-09-24: this
    whole read side had zero instrumentation (confirmed empty across
    Tempo/LangFuse/Mimir despite ~200 requests per scan from the
    frontend's poll loop), unlike the scan CREATE/execute path which
    already had it. One-shot session id per call — these are stateless
    polls, not a conversation, so there's no natural persistent id to
    reuse the way Ask has a thread_id.

    Supports both `with scan_read_span(...):` for a bounded call AND
    manual `.__enter__()`/`.__exit__()` for a span that must stay open
    across an SSE generator's full lifetime (same reason
    `ycs.query.ai.generate` uses manual enter/exit — a `with` block
    doesn't safely span a generator across client disconnects)."""
    session_id = f"rr-scan-read-{scan_id}-{uuid.uuid4().hex[:8]}"
    with infra.langfuse.service.session(
        "rr-scan-read", session_id = session_id, user_id = "default",
    ), infra.opentelemetry.service.get_tracer().start_as_current_span(
        f"rr.scan.{operation}",
        attributes = {
            infra.langfuse.keys.KEEP:                   True,
            infra.langfuse.keys.KIND:                   "workflow_root",
            infra.langfuse.keys.TRACE_NAME:                    f"rr.scan.{operation}",
            "rr.scan_id":                              scan_id,
            infra.langfuse.keys.OBS_META_WORKFLOW: "rr_scan_read",
        },
    ):
        yield
