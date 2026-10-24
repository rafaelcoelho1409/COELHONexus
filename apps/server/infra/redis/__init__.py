"""infra/redis — Redis connection config + client factories.

Every domain, entrypoint and the Celery broker config reach Redis through
here; no other module imports `redis`, reads `REDIS_*` env vars or builds a
Redis URL. Domains keep owning what they store (key names, channels, TTLs)."""
from . import domain, params, service


__all__ = ["domain", "params", "service"]
