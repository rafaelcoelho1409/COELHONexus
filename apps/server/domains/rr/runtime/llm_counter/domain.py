"""Pure functions for RR's per-scan LLM counter — no I/O, no event loop."""
from __future__ import annotations

from typing import Any


def pick_first_real_model(*candidates: Any) -> str | None:
    """Return the first non-empty model string.

    2026-09-17: dropped the old `_ROTATOR_GROUP_NAMES` filter (rr-strong,
    dd-all, dd-synth, dd-reduce-label, dd-keylm, dd-embed) — those were
    LiteLLM Router group-alias names from the superseded in-process
    multi-provider endpoint. The external endpoint always
    resolves the configured model to a real per-deployment string server-side
    (e.g. "NVIDIA/nvidia/nemotron-..."), so none of these candidates can
    ever equal one of those names anymore — the filter was pure dead
    weight."""
    for c in candidates:
        if isinstance(c, str) and c:
            return c
    return None


def extract_usage(response: Any) -> tuple[str | None, int, int]:
    """Return (model_id, tokens_in, tokens_out) from a LangChain LLMResult."""
    model_candidates: list[Any] = []
    tokens_in = 0
    tokens_out = 0

    llm_output = getattr(response, "llm_output", None)
    if isinstance(llm_output, dict):
        model_candidates += [
            llm_output.get("model_name"),
            llm_output.get("model"),
            llm_output.get("model_id"),
        ]
        token_usage = llm_output.get("token_usage") or {}
        if isinstance(token_usage, dict):
            tokens_in  = int(token_usage.get("prompt_tokens")     or tokens_in)
            tokens_out = int(token_usage.get("completion_tokens") or tokens_out)

    generations = getattr(response, "generations", None) or []
    if generations and generations[0]:
        gen = generations[0][0]
        message = getattr(gen, "message", None)
        if message is not None:
            um = getattr(message, "usage_metadata", None)
            if isinstance(um, dict):
                tokens_in  = int(um.get("input_tokens")  or tokens_in)
                tokens_out = int(um.get("output_tokens") or tokens_out)
            rm = getattr(message, "response_metadata", None) or {}
            if isinstance(rm, dict):
                model_candidates += [
                    rm.get("model_name"),
                    rm.get("model"),
                    rm.get("ls_model_name"),
                    rm.get("ls_provider"),
                ]
                raw = rm.get("response") or rm.get("model_extra") or {}
                if isinstance(raw, dict):
                    model_candidates += [raw.get("model"), raw.get("model_name")]
            ak = getattr(message, "additional_kwargs", None)
            if isinstance(ak, dict):
                model_candidates += [ak.get("model"), ak.get("model_name")]

    model_id = pick_first_real_model(*model_candidates)
    return model_id, max(0, tokens_in), max(0, tokens_out)
