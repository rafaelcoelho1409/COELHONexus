"""Qdrant client + dimension defaults.

Bind the dense vector size to the rotator's default embedding model
(`nvidia/llama-nemotron-embed-1b-v2` → 2048). The Qdrant collection
bootstrap reads this to size the dense vector slot."""
from __future__ import annotations

import os


# Standalone Qdrant deployment in the cluster ships with no auth.
QDRANT_HOST = os.environ.get("QDRANT_HOST", "qdrant")
QDRANT_PORT = int(os.environ.get("QDRANT_PORT", "6333"))
QDRANT_API_KEY = os.environ.get("QDRANT_API_KEY") or None
QDRANT_HTTPS = os.environ.get("QDRANT_HTTPS", "false").lower() in ("1", "true", "yes")

# Default dense vector size — aligns with the rotator's
# `nvidia/llama-nemotron-embed-1b-v2`. Used at collection-create time.
DEFAULT_DENSE_DIM = 2048

# Default connection timeouts.
TIMEOUT_S = 60.0
