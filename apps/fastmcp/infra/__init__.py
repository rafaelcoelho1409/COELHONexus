"""Cross-cutting infrastructure wrappers — mirrors apps/fastapi/infra/.

One sub-package per external system / SDK (otel/ today; future: ratelimit/
backed by Redis, langfuse/ for prompt management, etc.). NOT for business
logic — business code lives in apps/fastmcp/domains/.
"""
