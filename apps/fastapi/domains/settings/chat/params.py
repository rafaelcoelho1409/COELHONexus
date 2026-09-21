"""chat router — tunables + env names (no behavior)."""
from __future__ import annotations


# How often a worker re-reads the Settings store for endpoint changes.
ENDPOINT_RESOLVE_TTL_S: float = 10.0

# Largest per-request client ceiling. Individual calls pass their own
# `timeout` kwarg; this is only the fallback when none is given.
CLIENT_MAX_TIMEOUT_S: float = 90.0

# Shared httpx pool shape — lets concurrent node calls share TCP/keep-alive.
POOL_MAX_CONNECTIONS: int = 200
POOL_MAX_KEEPALIVE: int = 100
POOL_KEEPALIVE_EXPIRY_S: float = 30.0

# Env fallbacks, read at import. `LLM_ENDPOINT_*` is the current name;
# `COELHO_LLM_*` is the legacy name, still honored for deployed envs.
URL_ENVS: tuple[str, ...] = ("LLM_ENDPOINT_URL", "COELHO_LLM_ROTATOR_URL", "COELHO_LLM_URL")
MODEL_ENVS: tuple[str, ...] = ("LLM_MODEL", "COELHO_LLM_MODEL")
DEFAULT_MODEL: str = "auto"
