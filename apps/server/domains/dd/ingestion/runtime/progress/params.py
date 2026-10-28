from __future__ import annotations


TTL_S = 7200

# The per-framework lock is a short LEASE the running task keeps renewing (the cancel watcher does it every LOCK_RENEW_S), so a task
# that dies — a redeploy, an OOM kill, the Celery hard limit — frees the framework, and every other ingestion with it (one runs at a
# time), within LOCK_LEASE_S. It used to be one long TTL (3900 s) that outlived the worst-case run: a killed run blocked everything for
# 65 minutes. A run that is queued, waiting for a free worker, holds the longer LOCK_QUEUED_S instead, and takes the short lease when
# it starts.
LOCK_QUEUED_S = 1800
LOCK_LEASE_S = 300
LOCK_RENEW_S = 30

THROTTLE_S = 1.0
CANCEL_POLL_THROTTLE_S = 1.0

# A run is in flight while its progress says one of these (a finished run says `done`, `failed`, `cancelled`, `downgrade`…).
ACTIVE_STATUSES = ("running", "idle")
STATUS_IDLE = "idle"
STATUS_RUNNING = "running"
PHASE_INGEST = "ingest"

# What a progress record keeps of the long strings it is given: the URL last worked on, a recorded URL, an error, a note.
LAST_URL_MAX_CHARS = 200
URL_MAX_CHARS = 500
ERROR_MAX_CHARS = 500
NOTE_MAX_CHARS = 200
