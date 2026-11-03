from __future__ import annotations


# A page event carries the page's markdown so the UI fills in as the book is read; a late subscriber replays the snapshot. 3000 pages
# at most (`ingestion.params.MAX_PAGES`) plus the run's own events.
SNAPSHOT_MAX_EVENTS = 3500
SNAPSHOT_TTL_S = 86400
TTL_S = 86400

# A page's markdown in an event is cut here; the page object in the store has all of it.
EVENT_MARKDOWN_MAX_CHARS = 60_000

# The book's lock is a short lease the run keeps renewing after each slice, so a task that dies frees the book within one lease. A
# run that is queued, waiting for the worker, holds the longer LOCK_QUEUED_S and takes the short lease when it starts.
LOCK_QUEUED_S = 1800
LOCK_LEASE_S = 300

CANCEL_POLL_THROTTLE_S = 1.0
ERROR_MAX_CHARS = 500

# A run is in flight while its progress says one of these.
STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
ACTIVE_STATUSES = (STATUS_QUEUED, STATUS_RUNNING)
