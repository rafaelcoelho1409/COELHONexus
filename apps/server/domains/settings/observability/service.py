"""Client-side gen_ai.* spans and metric recorders around the chat/embeddings endpoint
adapters — the ONLY connection Nexus makes to an external LLM. Before this, every
outbound call showed up as a generic `httpx` POST span with no model/token/latency data and
no `gen_ai.*` attributes, so it never passed `infra.opentelemetry.domain.should_keep_span`'s
LangFuse allow-list either. Attribute and span names live in `keys.py`; the instruments are
registered in `infra.opentelemetry.entities.INSTRUMENTS` (gen_ai_call_duration,
gen_ai_call_total, gen_ai_usage_tokens).

Usage — attributes known only after the response arrives are set on the yielded span inside
the `with` block:

    with chat_completion_span(model=ENDPOINT.model) as span:
        resp = await client.chat.completions.create(**kwargs)
        if span is not None:
            record_chat_response(span, model=resp.model, usage=resp.usage)
"""
from __future__ import annotations
import infra
from . import keys


# ---------------------------------------------------------------------------
# Spans
# ---------------------------------------------------------------------------

def _chat_span_attrs(model: str, temperature: float | None, max_tokens: int | None) -> dict:
    return {
        keys.GEN_AI_SYSTEM:                   keys.SYSTEM_NEXUS_CHAT_ENDPOINT,
        keys.GEN_AI_OPERATION_NAME:           keys.OP_CHAT,
        keys.GEN_AI_REQUEST_MODEL:            model,
        keys.GEN_AI_REQUEST_TEMPERATURE:      temperature,
        keys.GEN_AI_REQUEST_MAX_TOKENS:       max_tokens,
        infra.langfuse.keys.OBSERVATION_TYPE: infra.langfuse.keys.OBSERVATION_TYPE_GENERATION,
        infra.langfuse.keys.KEEP:             True,
    }


def chat_completion_span(
    *,
    model:       str,
    temperature: float | None = None,
    max_tokens:  int | None   = None,
):
    """`gen_ai.chat` client span around one chat call (unset temperature / max_tokens are omitted)."""
    return infra.opentelemetry.service.client_span(
        keys.SPAN_NAME_CHAT, _chat_span_attrs(model, temperature, max_tokens),
    )


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
    tracer = infra.opentelemetry.service.get_tracer()
    if tracer is None:
        return None
    attrs = {
        k: v for k, v in _chat_span_attrs(model, temperature, max_tokens).items() if v is not None
    }
    return tracer.start_span(
        keys.SPAN_NAME_CHAT, kind = infra.opentelemetry.service.SpanKind.CLIENT, attributes = attrs,
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
            span.set_status(infra.opentelemetry.service.Status(
                infra.opentelemetry.service.StatusCode.ERROR, str(error),
            ))
        span.end()
    except Exception:
        pass


def embedding_span(*, model: str, input_count: int):
    """`gen_ai.embed` client span around one embeddings call."""
    return infra.opentelemetry.service.client_span(
        keys.SPAN_NAME_EMBED,
        {
            keys.GEN_AI_SYSTEM:                   keys.SYSTEM_NEXUS_EMBEDDING_ENDPOINT,
            keys.GEN_AI_OPERATION_NAME:           keys.OP_EMBEDDING,
            keys.GEN_AI_REQUEST_MODEL:            model,
            keys.GEN_AI_REQUEST_INPUT_COUNT:      input_count,
            infra.langfuse.keys.OBSERVATION_TYPE: infra.langfuse.keys.OBSERVATION_TYPE_EMBEDDING,
            infra.langfuse.keys.KEEP:             True,
        },
    )


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


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def record_gen_ai_call(
    *,
    operation:     str,
    model:         str,
    outcome:       str,
    duration_s:    float,
    input_tokens:  int | None = None,
    output_tokens: int | None = None,
) -> None:
    """`operation` ∈ {chat, embedding}; `outcome` ∈ {ok, timeout, error}."""
    try:
        labels = {"operation": operation, "model": model, "outcome": outcome}
        if (inst := infra.opentelemetry.service.get_instrument("gen_ai_call_duration")) is not None:
            inst.record(duration_s, attributes = labels)
        if (inst := infra.opentelemetry.service.get_instrument("gen_ai_call_total")) is not None:
            inst.add(1, attributes = labels)
        if input_tokens is not None:
            if (inst := infra.opentelemetry.service.get_instrument("gen_ai_usage_tokens")) is not None:
                inst.record(input_tokens, attributes = {
                    "operation": operation, "model": model, "token_type": "input",
                })
        if output_tokens is not None:
            if (inst := infra.opentelemetry.service.get_instrument("gen_ai_usage_tokens")) is not None:
                inst.record(output_tokens, attributes = {
                    "operation": operation, "model": model, "token_type": "output",
                })
    except Exception:
        pass
