"""langfuse patterns — pre-compiled regexes (no behavior)."""
from __future__ import annotations

import re


# LangFuse metadata keys must be safe path segments.
SAFE_KEY_RE = re.compile(r"[^a-zA-Z0-9_.-]+")

# The OTLP ingest path (and anything after it) that follows the base URL in `LANGFUSE_OTLP_ENDPOINT`.
OTLP_PATH_RE = re.compile(r"/api/public/otel.*$")
