from __future__ import annotations


# UI write propagates within 1 TTL; long enough that one node run (~30 reads) doesn't hammer MinIO.
CACHE_TTL_S: float = 30.0

# warm() is best-effort and runs from the async lifespan — it must never
# block app startup for minutes because MinIO is slow/unreachable. Short
# client-level timeouts are the primary control; this is the wall-clock
# backstop around the whole warm() call chain (up to ~6 sequential round
# trips: KEK resolve/autogen, reload, maybe-import-env re-reload + persist).
WARM_HARD_TIMEOUT_S: int = 15

# Opt-in switch for importing managed keys from env on warm
# (service._maybe_import_env_keys) — set to a truthy value to enable.
IMPORT_ENV_FLAG: str = "KD_CREDS_IMPORT_ENV"
