"""Async context-manager span helpers for the rotator chokepoints.

Each helper opens an OTel span pre-populated with gen_ai.* request attributes,
hands the caller a `GenAISpan` wrapper, and lets the caller attach response
attributes on success or capture an error on failure. Spans nest under
whatever LangGraph node span is currently active (set via the planner/synth
`@traced(...)` decorator), so per-feature filtering in LangFuse and Tempo
is inherited for free.

When `infra.otel.init_otel()` hasn't run, `get_tracer()` returns a no-op
tracer and all attribute writes are silently dropped — these helpers stay
safe to import and call.
"""
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
    """Wrapper around the active OTel span. All methods are no-ops when the
    underlying span is a no-op (tracer not initialized) — set_attribute on
    the OTel no-op span is already a no-op, so we just forward."""
    __slots__ = ("_span",)

    def __init__(self, span: Any) -> None:
        self._span = span

    def attach_attrs(self, attrs: dict[str, Any]) -> None:
        """Set multiple attributes; None values dropped (OTel rejects them)."""
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


# --------------------------------------------------------------------------- #
# Chat completion
# --------------------------------------------------------------------------- #
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
    """Wrap a chat-completion call. Caller calls `.attach_chat_response(r)`
    on success; exceptions are auto-recorded on the span."""
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


# --------------------------------------------------------------------------- #
# Embedding
# --------------------------------------------------------------------------- #
@asynccontextmanager
async def genai_embedding_span(
    *,
    request_model: str,
    texts: list[str],
    input_type: str | None = None,
    system: str = SYSTEM_LITELLM_ROTATOR,
) -> AsyncIterator[GenAISpan]:
    """Wrap an embedding batch. Caller calls
    `.attach_embedding_response(r)` on success."""
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
    """Sync equivalent of `genai_embedding_span` — for `embed_via_router_sync`
    which can't be awaited."""
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


# --------------------------------------------------------------------------- #
# Rerank
# --------------------------------------------------------------------------- #
@asynccontextmanager
async def genai_rerank_span(
    *,
    request_model: str,
    query: str,
    documents: list[str],
    system: str = SYSTEM_LITELLM_ROTATOR,
) -> AsyncIterator[GenAISpan]:
    """Wrap a rerank call. Caller calls `.attach_rerank_response(pairs)` on
    success."""
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


# --------------------------------------------------------------------------- #
# Bandit cascade — parent span + per-attempt children
# --------------------------------------------------------------------------- #
class BanditCascadeSpan:
    """Lightweight handle on the parent cascade span — caller updates
    total_attempts + fallback at the end of the cascade."""
    __slots__ = ("_span",)

    def __init__(self, span: Any) -> None:
        self._span = span

    def set_total_attempts(self, n: int) -> None:
        try:
            self._span.set_attribute(BANDIT_TOTAL_ATTEMPTS, int(n))
        except Exception:
            pass

    def set_fallback(self, reason: str | None) -> None:
        if not reason:
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
    """Parent span for the full bandit cascade. Per-attempt child spans are
    opened via `genai_bandit_attempt_span` inside the cascade loop."""
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
    """Per-attempt child generation span. Carries both gen_ai.* request
    attrs (model = deployment_id, system = provider prefix) AND
    bandit.* attempt metadata. Caller calls `.attach_chat_response(r)` on
    success and `update_bandit_outcome(span, ...)` on every attempt
    (success or fail) — see helpers below."""
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
    """Patch the bandit.* outcome attributes onto an open attempt span.
    Called from inside the cascade attempt block after each attempt
    completes (success OR failure) so we capture latency/reward/error_class
    consistently."""
    wrapper.attach_attrs({
        "bandit.latency_s":    latency_s,
        "bandit.reward":       reward,
        "bandit.error_class":  error_class,
        "bandit.schema_valid": schema_valid,
    })


# --------------------------------------------------------------------------- #
# Internal — error capture
# --------------------------------------------------------------------------- #
def _record_error(span: Any, exc: Exception) -> None:
    """Attach error.type / error.message + record_exception. No-op safe."""
    try:
        span.set_attribute("error.type", type(exc).__name__)
        span.set_attribute("error.message", str(exc)[:300])
        span.record_exception(exc)
    except Exception:
        pass
