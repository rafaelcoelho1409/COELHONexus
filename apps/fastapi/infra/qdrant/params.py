"""Qdrant client + dimension defaults.

Fallback dense vector size, used only when the configured embedding
endpoint can't be probed yet at collection-create time. The real
dimension is learned per endpoint via `domains.settings.embeddings`
probe at runtime — never trust this past the first probe."""
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

# Fallback only — `domains.ycs.embeddings.service.get_embedding_info()`
# probes the real dimension from the configured endpoint. Used at
# collection-create time when no probe has succeeded yet.
DEFAULT_DENSE_DIM = 2048

TIMEOUT_S = 60.0
