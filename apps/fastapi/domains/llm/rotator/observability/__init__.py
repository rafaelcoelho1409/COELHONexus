"""OTel gen_ai.* semconv span helpers; dual-exports to Tempo (Alloy) and LangFuse v3 via infra.otel TracerProvider."""
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
