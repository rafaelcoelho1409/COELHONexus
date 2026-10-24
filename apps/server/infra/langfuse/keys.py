"""langfuse keys — LangFuse OTel attribute namespace (no behavior).

Single source of truth for every `langfuse.*` / review-marker attribute
name stamped by `spans.py` and `service.py`. Consumed outside `service.py`,
hence its own file (§2 `keys.py` trigger).
"""
from __future__ import annotations


# I/O attributes — stamped at BOTH observation and trace level on
# workflow-root spans (see `service.set_current_span_langfuse_io`).
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

TAGS = "langfuse.tags"

# Baggage keys the OTel baggage processor may mirror onto spans so LangFuse
# promotes them to top-level session / user / tags on the trace.
BAGGAGE_KEYS: frozenset[str] = frozenset({SESSION_ID, USER_ID, TAGS})

# Domain-code span attributes: curated-export opt-in (read by the span gate,
# `domain.is_curated_keep`), trace name, observation type, and the workflow
# metadata promoted to top-level filters.
KEEP       = "coelho.langfuse.keep"
KIND       = "coelho.langfuse.kind"
TRACE_NAME = "langfuse.trace.name"
OBS_META_WORKFLOW  = OBS_META_PREFIX + "workflow"
OBS_META_NODE_NAME = OBS_META_PREFIX + "node_name"
OBS_META_STAGE     = OBS_META_PREFIX + "stage"

# Observation typing — an explicit type always wins over ingest inference, so
# chat spans classify as `generation` (cost + Metrics tab) even when
# conventions shift under us.
OBSERVATION_TYPE            = "langfuse.observation.type"
OBSERVATION_TYPE_GENERATION = "generation"
OBSERVATION_TYPE_EMBEDDING  = "embedding"

# Span-gate tables (`domain.should_keep_span`). Celery task root spans for
# these domains become the trace root in LangFuse and give the trace its name.
CELERY_DOMAIN_PREFIXES = (
    "run/domains.dd.",
    "run/domains.rr.",
    "run/domains.ycs.",
)

# Explicit deny, evaluated before any allow rule — MCP client transport
# spans (httpx calls to the FastMCP server) + JSON-RPC protocol-level spans.
MCP_TRANSPORT_DROPS = (
    "POST /mcp",
    "GET /mcp",
    "DELETE /mcp",
    "tools/list",
    "tools/call",
)

# Human-review markers (`service.flag_for_review`).
REVIEW_REQUIRED_ATTR   = "review.required"
REVIEW_REASON_ATTR     = "review.reason"
REVIEW_SEVERITY_ATTR   = "review.severity"
REVIEW_REQUIRED_SCORE  = "review.required"
