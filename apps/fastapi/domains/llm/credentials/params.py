from __future__ import annotations


# Short enough that a UI write in FastAPI propagates to the Celery worker
# within one TTL; long enough that one rotator build (~30 reads) doesn't
# hammer MinIO.
CACHE_TTL_S: float = 30.0
