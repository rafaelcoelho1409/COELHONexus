"""Client-side gen_ai.* spans around the chat/embeddings endpoint adapters
— the ONLY connection Nexus makes to an external LLM. Before this, every
outbound call showed up as a generic `httpx` POST span with no model/
token/latency data and no `gen_ai.*` attributes, so it never passed
`infra.otel.domain.should_keep_span`'s LangFuse allow-list either.

Usage — attributes known only after the response arrives are set on the
yielded span inside the `with` block:

    with chat_completion_span(model=ENDPOINT.model) as span:
        resp = await client.chat.completions.create(**kwargs)
        if span is not None:
            record_chat_response(span, model=resp.model, usage=resp.usage)
"""
from __future__ import annotations
import infra
from . import keys

import contextlib
from collections.abc import Iterator

from opentelemetry import trace


@contextlib.contextmanager
def chat_completion_span(
    *,
    model:       str,
    temperature: float | None = None,
    max_tokens:  int | None   = None,
) -> Iterator[object | None]:
    tracer = infra.otel.service.get_tracer()
    if tracer is None:
        yield None
        return
    attrs: dict = {
        keys.GEN_AI_SYSTEM:         keys.SYSTEM_NEXUS_CHAT_ENDPOINT,
        keys.GEN_AI_OPERATION_NAME: keys.OP_CHAT,
        keys.GEN_AI_REQUEST_MODEL:  model,
        keys.LANGFUSE_OBSERVATION_TYPE: keys.OBSERVATION_TYPE_GENERATION,
        "coelho.langfuse.keep":     True,
    }
    if temperature is not None:
        attrs[keys.GEN_AI_REQUEST_TEMPERATURE] = temperature
    if max_tokens is not None:
        attrs[keys.GEN_AI_REQUEST_MAX_TOKENS] = max_tokens
    with tracer.start_as_current_span(
        keys.SPAN_NAME_CHAT,
        kind       = trace.SpanKind.CLIENT,
        attributes = attrs,
    ) as span:
        try:
            yield span
        except Exception as e:
            span.set_attribute("error.type", type(e).__name__)
            span.record_exception(e)
            raise


def record_chat_response(
    span: object | None,
    *,
    model:         str,
    input_tokens:  int | None = None,
    output_tokens: int | None = None,
) -> None:
    """Enrich an open `chat_completion_span` once the response is known."""
    if span is None:
        return
    span.set_attribute(keys.GEN_AI_RESPONSE_MODEL, model)
    if input_tokens is not None:
        span.set_attribute(keys.GEN_AI_USAGE_INPUT_TOKENS, input_tokens)
    if output_tokens is not None:
        span.set_attribute(keys.GEN_AI_USAGE_OUTPUT_TOKENS, output_tokens)


def start_chat_span(
    *,
    model:       str,
    temperature: float | None = None,
    max_tokens:  int | None   = None,
) -> object | None:
    """Split-lifecycle counterpart to `chat_completion_span`, for a
    caller that can't wrap the whole call in one `with` block — e.g. a
    LangChain callback (`on_llm_start`/`on_llm_end`/`on_llm_error` are
    three separate invocations, and under concurrency they can
    interleave with OTHER calls' start/end pairs on the same callback
    instance).

    Deliberately uses `start_span`, not `start_as_current_span`: the
    latter attaches the span to the ambient context and expects the
    SAME `with` block (same coroutine, same await chain) to detach it
    — splitting attach/detach across two independent async callback
    invocations trips OTel's context-detach mismatch check under
    concurrency. `start_span` creates the span without touching the
    ambient context, so the caller is responsible for its own
    correlation (e.g. keying it by LangChain's `run_id`) and must pair
    every call with `end_chat_span`."""
    tracer = infra.otel.service.get_tracer()
    if tracer is None:
        return None
    attrs: dict = {
        keys.GEN_AI_SYSTEM:         keys.SYSTEM_NEXUS_CHAT_ENDPOINT,
        keys.GEN_AI_OPERATION_NAME: keys.OP_CHAT,
        keys.GEN_AI_REQUEST_MODEL:  model,
        keys.LANGFUSE_OBSERVATION_TYPE: keys.OBSERVATION_TYPE_GENERATION,
        "coelho.langfuse.keep":     True,
    }
    if temperature is not None:
        attrs[keys.GEN_AI_REQUEST_TEMPERATURE] = temperature
    if max_tokens is not None:
        attrs[keys.GEN_AI_REQUEST_MAX_TOKENS] = max_tokens
    return tracer.start_span(
        keys.SPAN_NAME_CHAT, kind = trace.SpanKind.CLIENT, attributes = attrs,
    )


def end_chat_span(span: object | None, *, error: BaseException | None = None) -> None:
    """Close a span opened by `start_chat_span`. `start_as_current_span`
    (used by `chat_completion_span`) sets error status/records the
    exception automatically on `with`-block exit; `start_span` does
    neither, so both are done here explicitly on the error path."""
    if span is None:
        return
    try:
        if error is not None:
            span.set_attribute("error.type", type(error).__name__)
            span.record_exception(error)
            span.set_status(trace.Status(trace.StatusCode.ERROR, str(error)))
        span.end()
    except Exception:
        pass


@contextlib.contextmanager
def embedding_span(
    *,
    model:      str,
    input_count: int,
) -> Iterator[object | None]:
    tracer = infra.otel.service.get_tracer()
    if tracer is None:
        yield None
        return
    with tracer.start_as_current_span(
        keys.SPAN_NAME_EMBED,
        kind       = trace.SpanKind.CLIENT,
        attributes = {
            keys.GEN_AI_SYSTEM:              keys.SYSTEM_NEXUS_EMBEDDING_ENDPOINT,
            keys.GEN_AI_OPERATION_NAME:      keys.OP_EMBEDDING,
            keys.GEN_AI_REQUEST_MODEL:       model,
            keys.GEN_AI_REQUEST_INPUT_COUNT: input_count,
            keys.LANGFUSE_OBSERVATION_TYPE:  keys.OBSERVATION_TYPE_EMBEDDING,
            "coelho.langfuse.keep":          True,
        },
    ) as span:
        try:
            yield span
        except Exception as e:
            span.set_attribute("error.type", type(e).__name__)
            span.record_exception(e)
            raise


def record_embedding_response(
    span: object | None,
    *,
    model:        str,
    vector_count: int,
) -> None:
    """Enrich an open `embedding_span` once the response is known."""
    if span is None:
        return
    span.set_attribute(keys.GEN_AI_RESPONSE_MODEL, model)
    span.set_attribute(keys.GEN_AI_RESPONSE_EMBEDDING_VECTORS, vector_count)
