"""Qdrant client + dimension defaults.

Bind the dense vector size to the rotator's default embedding model
(`nvidia/llama-nemotron-embed-1b-v2` → 2048). The Qdrant collection
bootstrap reads this to size the dense vector slot."""
from __future__ import annotations

import os


# Standalone Qdrant deployment in the cluster ships with no auth.
# Honor `QDRANT_URL` as the primary configuration knob (matches what
# the Helm chart + Celery `qdrant_task` use), falling back to the
# `QDRANT_HOST`/`PORT`/`HTTPS` triplet only when URL isn't set.
# Previously only the host/port path existed, defaulting to
# bare `"qdrant"` — which resolves inside the qdrant namespace but
# NOT from the coelhonexus-dev namespace where the fastapi pod runs.
# The AsyncQdrantClient then failed with "All connection attempts
# failed" on every retrieval, silently demoting `SmartRetriever` to
# ES-only and starving the agent's grader.
from urllib.parse import urlparse

_QDRANT_URL_RAW = os.environ.get("QDRANT_URL", "").strip()
if _QDRANT_URL_RAW:
    _parsed = urlparse(_QDRANT_URL_RAW)
    QDRANT_HOST  = _parsed.hostname or "qdrant"
    QDRANT_PORT  = _parsed.port or 6333
    QDRANT_HTTPS = _parsed.scheme.lower() == "https"
else:
    QDRANT_HOST  = os.environ.get("QDRANT_HOST", "qdrant")
    QDRANT_PORT  = int(os.environ.get("QDRANT_PORT", "6333"))
    QDRANT_HTTPS = os.environ.get("QDRANT_HTTPS", "false").lower() in ("1", "true", "yes")

QDRANT_API_KEY = os.environ.get("QDRANT_API_KEY") or None

# `nvidia/llama-nemotron-embed-1b-v2`. Used at collection-create time.
DEFAULT_DENSE_DIM = 2048

TIMEOUT_S = 60.0
