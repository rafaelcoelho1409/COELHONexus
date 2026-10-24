"""opentelemetry keys — the baggage key allow-list and span attribute names.

The allow-list is consumed by domain.py's baggage predicate, not by service.py
directly — that's what puts it here instead of params.py. Pure lookup data.
(The LangFuse span-gate tables live in `infra.langfuse.keys`.)
"""
from __future__ import annotations


# Span attribute set on the request span that enqueued a Celery task
# (`service.tag_celery_task`).
CELERY_TASK_ID = "celery.task_id"


# BaggageSpanProcessor allow-list — keeps high-cardinality values from
# accidentally exploding Tempo/LangFuse storage. See
# domain.is_allowed_baggage_key. The LangFuse session / user / tags keys are
# added from `infra.langfuse.keys.BAGGAGE_KEYS`.
ALLOWED_BAGGAGE_KEYS: frozenset[str] = frozenset({
    "study_id",
    "framework",
    "channel_id",
    "digest_id",
    "chapter_id",
    "tenant",
    "arm_name",
    "session_id",
    "user_id",
    "pipeline",
})
