"""OTel span helpers for synth nodes — mirrors planner/observability/service.

`@traced("name")` wraps a node into a top-level OTel span (chapter_id included for per-chapter Gantt).
`attach_span_attrs(prefix, attrs)` attaches a stats dict to the currently-active span.
"""
from __future__ import annotations

import functools
import logging
from typing import Awaitable, Callable

from opentelemetry import trace as _otel_trace

from infra.otel import get_tracer
from domains.dd.runtime.llm_counter import get_context, set_context


logger = logging.getLogger(__name__)


def traced(name: str) -> Callable:
    """Decorator: turn an async node coroutine into a top-level OTel span."""
    def decorator(fn: Callable[..., Awaitable[dict]]):
        @functools.wraps(fn)
        async def wrapper(state: dict, *args, **kwargs) -> dict:
            tracer = get_tracer()
            prev_stage, prev_thread_id, prev_node_id = get_context()
            set_context(
                stage="synth",
                thread_id=state.get("thread_id") or "",
                node_id=name,
            )
            if tracer is None:
                try:
                    return await fn(state, *args, **kwargs)
                finally:
                    set_context(
                        stage=prev_stage,
                        thread_id=prev_thread_id,
                        node_id=prev_node_id,
                    )
            attrs = {
                "coelho.langfuse.keep": True,
                "coelho.langfuse.kind": "workflow_node",
                "synth.node":           name,
                "synth.thread_id":      state.get("thread_id") or "",
                "synth.framework_slug": state.get("framework_slug") or "",
                "synth.chapter_id":     state.get("chapter_id") or "",
                "langfuse.observation.metadata.workflow": "dd_synth",
                "langfuse.observation.metadata.node_name": name,
                "langfuse.observation.metadata.stage": "synth",
            }
            with tracer.start_as_current_span(
                f"dd.synth.node.{name}", attributes = attrs,
            ) as span:
                try:
                    result = await fn(state, *args, **kwargs)
                    span.set_attribute("synth.ok", True)
                    return result
                except Exception as e:
                    span.set_attribute("synth.ok", False)
                    span.set_attribute("synth.error_type", type(e).__name__)
                    span.set_attribute("synth.error_message", str(e)[:200])
                    span.record_exception(e)
                    raise
                finally:
                    set_context(
                        stage=prev_stage,
                        thread_id=prev_thread_id,
                        node_id=prev_node_id,
                    )
        return wrapper
    return decorator


def attach_span_attrs(prefix: str, attrs: dict) -> None:
    """Set namespaced attributes on the current OTel span; no-op if uninitialized; None values skipped."""
    try:
        span = _otel_trace.get_current_span()
        if hasattr(span, "is_recording") and not span.is_recording():
            return
        for k, v in attrs.items():
            if v is None:
                continue
            span.set_attribute(f"{prefix}.{k}", v)
    except Exception:
        pass
