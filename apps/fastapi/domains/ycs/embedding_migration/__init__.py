"""ycs/embedding_migration — consent-gated re-embed when the configured
embedding model changes. See `service.py` for the full design."""
from .service import (
    check_migration_needed,
    check_migration_needed_now,
    cutover,
    dispatch_migration,
    get_active_collection_name,
    get_migration_state,
    start_migration,
)


__all__ = [
    "check_migration_needed",
    "check_migration_needed_now",
    "cutover",
    "dispatch_migration",
    "get_active_collection_name",
    "get_migration_state",
    "start_migration",
]
