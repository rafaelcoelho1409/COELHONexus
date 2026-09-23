"""langfuse keys — LangFuse OTel attribute namespace (no behavior).

Single source of truth for every `langfuse.*` / review-marker attribute
name stamped by `spans.py` and `service.py`. Consumed outside `service.py`,
hence its own file (§2 `keys.py` trigger).
"""
from __future__ import annotations


# I/O attributes — stamped at BOTH observation and trace level on
# workflow-root spans (see `spans.set_current_span_langfuse_io`).
TRACE_INPUT  = "langfuse.trace.input"
TRACE_OUTPUT = "langfuse.trace.output"
OBS_INPUT    = "langfuse.observation.input"
OBS_OUTPUT   = "langfuse.observation.output"

# Metadata prefixes — selected workflow fields are promoted under these
# so they land top-level and filterable instead of the catch-all bucket.
TRACE_META_PREFIX = "langfuse.trace.metadata."
OBS_META_PREFIX   = "langfuse.observation.metadata."

# Session grouping — mirrored into OTel baggage by `service.session`.
# LangFuse v3 only promotes the dotted forms, hence both are stamped.
SESSION_ID = "langfuse.session.id"
USER_ID    = "langfuse.user.id"

# Human-review markers (`service.flag_for_review`).
REVIEW_REQUIRED_ATTR   = "review.required"
REVIEW_REASON_ATTR     = "review.reason"
REVIEW_SEVERITY_ATTR   = "review.severity"
REVIEW_REQUIRED_SCORE  = "review.required"
