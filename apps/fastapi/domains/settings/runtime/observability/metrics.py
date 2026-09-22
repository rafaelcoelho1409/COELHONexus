"""Metric recorders for the chat/embeddings gen_ai.* boundary — instruments
registered in `infra.otel.entities.INSTRUMENTS` (gen_ai_call_duration,
gen_ai_call_total, gen_ai_usage_tokens)."""
from __future__ import annotations
import infra


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
        if (inst := infra.otel.service.get_instrument("gen_ai_call_duration")) is not None:
            inst.record(duration_s, attributes = labels)
        if (inst := infra.otel.service.get_instrument("gen_ai_call_total")) is not None:
            inst.add(1, attributes = labels)
        if input_tokens is not None:
            if (inst := infra.otel.service.get_instrument("gen_ai_usage_tokens")) is not None:
                inst.record(input_tokens, attributes = {
                    "operation": operation, "model": model, "token_type": "input",
                })
        if output_tokens is not None:
            if (inst := infra.otel.service.get_instrument("gen_ai_usage_tokens")) is not None:
                inst.record(output_tokens, attributes = {
                    "operation": operation, "model": model, "token_type": "output",
                })
    except Exception:
        pass
