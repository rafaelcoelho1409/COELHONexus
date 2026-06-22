"""Async context-manager span helpers; spans nest under the active `@traced(...)` LangGraph node. Safe to call when OTel is uninitialized (no-op tracer)."""
from __future__ import annotations

from contextlib import asynccontextmanager, contextmanager
from typing import Any, AsyncIterator, Iterator

from infra.otel import get_tracer

from .domain import (
    build_bandit_attempt_attrs,
    build_bandit_cascade_attrs,
    build_chat_request_attrs,
    build_chat_response_attrs,
    build_embedding_request_attrs,
    build_embedding_response_attrs,
    build_rerank_request_attrs,
    build_rerank_response_attrs,
    system_for_deployment,
)
from .keys import (
    BANDIT_FALLBACK,
    BANDIT_TOTAL_ATTEMPTS,
    SPAN_NAME_BANDIT_ATTEMPT,
    SPAN_NAME_BANDIT_CASCADE,
    SPAN_NAME_CHAT,
    SPAN_NAME_EMBED,
    SPAN_NAME_RERANK,
    SYSTEM_LITELLM_ROTATOR,
)


class GenAISpan:
    """Wrapper around the active OTel span; forwards to no-op span when tracer is uninitialized."""
    __slots__ = ("_span",)

    def __init__(self, span: Any) -> None:
        self._span = span

    def _is_recording(self) -> bool:
        try:
            is_recording = getattr(self._span, "is_recording", None)
            if callable(is_recording):
                return bool(is_recording())
        except Exception:
            return False
        return True

    def attach_attrs(self, attrs: dict[str, Any]) -> None:
        """None values dropped (OTel rejects them)."""
        if not self._is_recording():
            return
        for k, v in attrs.items():
            if v is None:
                continue
            try:
                self._span.set_attribute(k, v)
            except Exception:
                pass

    def attach_chat_response(self, response: Any) -> None:
        self.attach_attrs(build_chat_response_attrs(response))

    def attach_embedding_response(self, response: Any) -> None:
        self.attach_attrs(build_embedding_response_attrs(response))

    def attach_rerank_response(self, rankings: list[tuple[int, float]] | None) -> None:
        self.attach_attrs(build_rerank_response_attrs(rankings))


@asynccontextmanager
async def genai_completion_span(
    *,
    request_model: str,
    messages: list[dict],
    temperature: float | None = None,
    max_tokens: int | None = None,
    top_p: float | None = None,
    system: str = SYSTEM_LITELLM_ROTATOR,
) -> AsyncIterator[GenAISpan]:
    """Caller calls `.attach_chat_response(r)` on success; exceptions auto-recorded."""
    tracer = get_tracer()
    attrs = build_chat_request_attrs(
        request_model = request_model,
        messages      = messages,
        temperature   = temperature,
        max_tokens    = max_tokens,
        top_p         = top_p,
        system        = system,
    )
    with tracer.start_as_current_span(SPAN_NAME_CHAT, attributes = attrs) as span:
        wrapper = GenAISpan(span)
        try:
            yield wrapper
        except Exception as e:
            _record_error(span, e)
            raise


@asynccontextmanager
async def genai_embedding_span(
    *,
    request_model: str,
    texts: list[str],
    input_type: str | None = None,
    system: str = SYSTEM_LITELLM_ROTATOR,
) -> AsyncIterator[GenAISpan]:
    """Caller calls `.attach_embedding_response(r)` on success."""
    tracer = get_tracer()
    attrs = build_embedding_request_attrs(
        request_model = request_model,
        texts         = texts,
        input_type    = input_type,
        system        = system,
    )
    with tracer.start_as_current_span(SPAN_NAME_EMBED, attributes = attrs) as span:
        wrapper = GenAISpan(span)
        try:
            yield wrapper
        except Exception as e:
            _record_error(span, e)
            raise


@contextmanager
def genai_embedding_span_sync(
    *,
    request_model: str,
    texts: list[str],
    input_type: str | None = None,
    system: str = SYSTEM_LITELLM_ROTATOR,
) -> Iterator[GenAISpan]:
    """Sync variant for `embed_via_router_sync` which can't be awaited."""
    tracer = get_tracer()
    attrs = build_embedding_request_attrs(
        request_model = request_model,
        texts         = texts,
        input_type    = input_type,
        system        = system,
    )
    with tracer.start_as_current_span(SPAN_NAME_EMBED, attributes = attrs) as span:
        wrapper = GenAISpan(span)
        try:
            yield wrapper
        except Exception as e:
            _record_error(span, e)
            raise


@asynccontextmanager
async def genai_rerank_span(
    *,
    request_model: str,
    query: str,
    documents: list[str],
    system: str = SYSTEM_LITELLM_ROTATOR,
) -> AsyncIterator[GenAISpan]:
    """Caller calls `.attach_rerank_response(pairs)` on success."""
    tracer = get_tracer()
    attrs = build_rerank_request_attrs(
        request_model = request_model,
        query         = query,
        documents     = documents,
        system        = system,
    )
    with tracer.start_as_current_span(SPAN_NAME_RERANK, attributes = attrs) as span:
        wrapper = GenAISpan(span)
        try:
            yield wrapper
        except Exception as e:
            _record_error(span, e)
            raise


class BanditCascadeSpan:
    """Parent cascade-span handle; caller updates total_attempts + fallback at the end."""
    __slots__ = ("_span",)

    def __init__(self, span: Any) -> None:
        self._span = span

    def _is_recording(self) -> bool:
        try:
            is_recording = getattr(self._span, "is_recording", None)
            if callable(is_recording):
                return bool(is_recording())
        except Exception:
            return False
        return True

    def set_total_attempts(self, n: int) -> None:
        if not self._is_recording():
            return
        try:
            self._span.set_attribute(BANDIT_TOTAL_ATTEMPTS, int(n))
        except Exception:
            pass

    def set_fallback(self, reason: str | None) -> None:
        if not reason or not self._is_recording():
            return
        try:
            self._span.set_attribute(BANDIT_FALLBACK, reason)
        except Exception:
            pass


@asynccontextmanager
async def genai_bandit_cascade_span(
    *,
    dd_process: str,
) -> AsyncIterator[BanditCascadeSpan]:
    """Per-attempt children are opened via `genai_bandit_attempt_span` inside the cascade loop."""
    tracer = get_tracer()
    attrs = build_bandit_cascade_attrs(dd_process = dd_process)
    with tracer.start_as_current_span(SPAN_NAME_BANDIT_CASCADE, attributes = attrs) as span:
        handle = BanditCascadeSpan(span)
        try:
            yield handle
        except Exception as e:
            _record_error(span, e)
            raise


@asynccontextmanager
async def genai_bandit_attempt_span(
    *,
    deployment_id: str,
    attempt: int,
    dd_process: str | None = None,
    messages: list[dict] | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> AsyncIterator[GenAISpan]:
    """Carries gen_ai.* request attrs AND bandit.* attempt metadata. Caller must call `update_bandit_outcome` on every attempt (success OR failure)."""
    tracer = get_tracer()
    system = system_for_deployment(deployment_id)
    attrs: dict[str, Any] = build_chat_request_attrs(
        request_model = deployment_id,
        messages      = messages or [],
        temperature   = temperature,
        max_tokens    = max_tokens,
        system        = system,
    )
    attrs.update(build_bandit_attempt_attrs(
        deployment_id = deployment_id,
        attempt       = attempt,
        dd_process    = dd_process,
    ))
    with tracer.start_as_current_span(SPAN_NAME_BANDIT_ATTEMPT, attributes = attrs) as span:
        wrapper = GenAISpan(span)
        try:
            yield wrapper
        except Exception as e:
            _record_error(span, e)
            raise


def update_bandit_outcome(
    wrapper: GenAISpan,
    *,
    latency_s: float | None = None,
    reward: float | None = None,
    error_class: str | None = None,
    schema_valid: bool | None = None,
) -> None:
    """Patch bandit.* outcome attrs onto an open attempt span (call on every attempt, success or fail)."""
    wrapper.attach_attrs({
        "bandit.latency_s":    latency_s,
        "bandit.reward":       reward,
        "bandit.error_class":  error_class,
        "bandit.schema_valid": schema_valid,
    })


def _record_error(span: Any, exc: Exception) -> None:
    """Attach error.type / error.message + record_exception (no-op safe)."""
    try:
        is_recording = getattr(span, "is_recording", None)
        if callable(is_recording) and not is_recording():
            return
    except Exception:
        return
    try:
        span.set_attribute("error.type", type(exc).__name__)
        span.set_attribute("error.message", str(exc)[:300])
        span.record_exception(exc)
    except Exception:
        pass
