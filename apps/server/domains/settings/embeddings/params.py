"""embeddings router — tunables + env names (no behavior)."""
from __future__ import annotations


# How often a worker re-reads the Settings store for endpoint changes.
ENDPOINT_RESOLVE_TTL_S: float = 10.0

# Env fallbacks, read at import. `EMBEDDING_ENDPOINT_*` is the current
# name; `COELHO_EMBEDDING_*` is the legacy name, still honored.
URL_ENVS: tuple[str, ...] = ("EMBEDDING_ENDPOINT_URL", "COELHO_EMBEDDING_URL")
MODEL_ENVS: tuple[str, ...] = ("EMBEDDING_MODEL", "COELHO_EMBEDDING_MODEL")
DEFAULT_MODEL: str = "auto"

# Settings-page "Test" button probe (embed_probe_async) defaults.
PROBE_TEXT: str = "connection test"
PROBE_TIMEOUT_S: float = 20.0
