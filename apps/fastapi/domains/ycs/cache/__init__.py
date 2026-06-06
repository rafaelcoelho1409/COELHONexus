"""ycs/cache — Redis SHA-256 response cache.

Direct port of deprecated `services/youtube/cache.py`. Prefix +
deprecated TTL preserved verbatim so existing Redis state is reused."""
from .keys import cache_key
from .params import CACHE_PREFIX, DEFAULT_TTL_S
from .service import (
    cache_response,
    get_cached_response,
    invalidate_cache,
)


__all__ = [
    "CACHE_PREFIX",
    "DEFAULT_TTL_S",
    "cache_key",
    "cache_response",
    "get_cached_response",
    "invalidate_cache",
]
