from __future__ import annotations


# Short enough for a UI write to propagate to Celery within one TTL;
# long enough that one rotator build (~30 reads) doesn't hammer MinIO.
CACHE_TTL_S: float = 30.0
