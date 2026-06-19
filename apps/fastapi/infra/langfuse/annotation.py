"""Annotation / human-review flagging — add a marker to the active
trace so the LangFuse UI can surface it for review.

Pattern: when a node detects a low-confidence outcome (e.g. planner
chapter_assign rescue), call `flag_for_review("rescued N docs")` inside
the node's @traced scope. The active span gets attributes
`review.required=true`, `review.reason=...`, `review.severity=...`
and a `review.required` score lands on the trace. Filter the LangFuse
UI by either signal to find traces needing human attention.

Fail-soft: never raises. If OTel isn't initialized or LangFuse isn't
available, this is a no-op + a debug log.
"""
from __future__ import annotations

import logging
from typing import Literal


logger = logging.getLogger(__name__)


Severity = Literal["low", "medium", "high"]


def flag_for_review(
    reason: str,
    *,
    severity: Severity = "low",
    score:    float | None = 1.0,
) -> None:
    """Tag the active trace as needing review. Set span attributes +
    optionally record a `review.required` score."""
    try:
        from opentelemetry import trace
        span = trace.get_current_span()
        span.set_attribute("review.required",  True)
        span.set_attribute("review.reason",    reason[:240])
        span.set_attribute("review.severity",  severity)
    except Exception as e:
        logger.debug(
            f"[langfuse-annotation] span tag failed: "
            f"{type(e).__name__}: {e}"
        )
    if score is not None:
        try:
            from .scores import record_score
            record_score(
                "review.required", float(score),
                comment = f"{severity}: {reason[:200]}",
            )
        except Exception as e:
            logger.debug(
                f"[langfuse-annotation] score write failed: "
                f"{type(e).__name__}: {e}"
            )
