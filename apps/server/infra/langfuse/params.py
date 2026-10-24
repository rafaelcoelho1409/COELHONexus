"""langfuse params — connection env and tunables (no behavior).

Soft env reads: `service.is_available()` / `get_client()` / `add_exporter()` check for what they need and fail soft."""
from __future__ import annotations

import os


# `HOST` is the SDK base URL (e.g. http://langfuse-web.langfuse.svc.cluster.local:3000); when unset it is derived from
# `OTLP_ENDPOINT` (see `domain.host_from_otlp_endpoint`), so one env var can configure both the exporter and the SDK.
HOST          = os.environ.get("LANGFUSE_HOST", "")
OTLP_ENDPOINT = os.environ.get("LANGFUSE_OTLP_ENDPOINT", "")
PUBLIC_KEY    = os.environ.get("LANGFUSE_PUBLIC_KEY", "")   # project public key (HTTP Basic user)
SECRET_KEY    = os.environ.get("LANGFUSE_SECRET_KEY", "")   # project secret key (HTTP Basic password)

# Prompt-template cache TTL (prompt label deployments change rarely;
# 60s bounds staleness without re-fetching per call).
PROMPT_CACHE_TTL_S = 60

# OTLP/HTTP export timeout to LangFuse (`LANGFUSE_OTLP_TIMEOUT` overrides). 10s timed out under heavy LLM volume;
# rich batches are slow to ingest.
OTLP_TIMEOUT_DEFAULT_S = 30
OTLP_TIMEOUT_S = int(os.environ.get("LANGFUSE_OTLP_TIMEOUT", str(OTLP_TIMEOUT_DEFAULT_S)))

# Span-attribute size caps — keep trace payloads bounded.
JSON_ATTR_CAP = 12_000
METADATA_VALUE_CAP = 512
