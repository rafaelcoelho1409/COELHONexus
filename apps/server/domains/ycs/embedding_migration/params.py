"""ycs/embedding_migration — tunables."""
from __future__ import annotations


# Redis TTL for the migration-state blob. Long enough to outlive a slow
# migration over a large corpus; short enough that an abandoned/crashed
# migration doesn't wedge the dispatch gate forever with no visible reason —
# after this window, `check_migration_needed` will simply detect the
# mismatch again on the next dispatch attempt and let the user re-trigger.
MIGRATION_STATE_TTL_S = 7 * 24 * 3600
