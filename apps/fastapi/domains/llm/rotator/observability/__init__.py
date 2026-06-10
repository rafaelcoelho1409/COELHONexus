"""OTel gen_ai.* semconv span helpers for the rotator chokepoints.

Spans emitted here flow through `infra.otel`'s TracerProvider, which dual-
exports to Alloy (Tempo) AND LangFuse v3 (LLM observations). One wrap,
two backends — no separate LangFuse SDK needed.

Per-feature filtering (DD vs YCS vs Radar) is inherited from the parent
`@traced(name)` span on the LangGraph node — no ContextVar plumbing
required at this layer.
"""
from .service import (
    BanditCascadeSpan,
    GenAISpan,
    genai_bandit_attempt_span,
    genai_bandit_cascade_span,
    genai_completion_span,
    genai_embedding_span,
    genai_embedding_span_sync,
    genai_rerank_span,
    update_bandit_outcome,
)


__all__ = [
    "BanditCascadeSpan",
    "GenAISpan",
    "genai_bandit_attempt_span",
    "genai_bandit_cascade_span",
    "genai_completion_span",
    "genai_embedding_span",
    "genai_embedding_span_sync",
    "genai_rerank_span",
    "update_bandit_outcome",
]
