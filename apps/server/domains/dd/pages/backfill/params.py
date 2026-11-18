"""Backfill — tunable scalars."""
from __future__ import annotations


# ~8 keeps MinIO + local CPU saturated without flooding (parse + hash bound, not I/O).
CONCURRENCY = 8
