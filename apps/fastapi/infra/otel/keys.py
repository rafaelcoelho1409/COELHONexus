"""otel keys — task-path tables, route/method-name tables, and the baggage
key allow-list.

All three are consumed by domain.py's span-gate and baggage predicates, not
by service.py directly — that's what puts them here instead of params.py.
Pure lookup data; no builder functions needed since nothing here is
assembled from parts, just enumerated.
"""
from __future__ import annotations

# Celery task root spans for these domains become the trace root in
# LangFuse and give the trace its name — see domain.is_curated_keep.
CELERY_DOMAIN_PREFIXES = (
    "run/domains.dd.",
    "run/domains.rr.",
    "run/domains.ycs.",
)

# Explicit deny, evaluated before any allow rule — MCP client transport
# spans (httpx calls to the FastMCP server) + JSON-RPC protocol-level
# spans. See domain.is_mcp_transport_drop.
MCP_TRANSPORT_DROPS = (
    "POST /mcp",
    "GET /mcp",
    "DELETE /mcp",
    "tools/list",
    "tools/call",
)


# BaggageSpanProcessor allow-list — keeps high-cardinality values from
# accidentally exploding Tempo/LangFuse storage. See
# domain.is_allowed_baggage_key.
ALLOWED_BAGGAGE_KEYS: frozenset[str] = frozenset({
    "study_id",
    "framework",
    "channel_id",
    "digest_id",
    "tenant",
    "arm_name",
    "session_id",
    "user_id",
    "pipeline",
    # LangFuse v3 OTel ingester promotes these to top-level session_id /
    # user_id / tags on the trace (separate from plain `session_id` which
    # only lands as a span attribute). Keep both — same value, two names.
    "langfuse.session.id",
    "langfuse.user.id",
    "langfuse.tags",
})
