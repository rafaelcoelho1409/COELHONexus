"""ycs/neo4j_task — tunables (no behavior)."""
from __future__ import annotations


# 2026-09-13: replaces the old MAX_ARM_SWAPS. There is no pool to swap
# across — retries just re-run failed videos on the same connection,
# same model, giving transient failures (504s, timeouts) another
# chance. 3 retries = 4 total attempts per video.
MAX_RETRY_PASSES: int = 3
