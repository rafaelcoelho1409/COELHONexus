"""ycs/admin — BFF helpers for the FastHTML wizard.

Three GET endpoints supporting the Wave 5 UI:
  GET /admin/ingested-channels   — ES terms aggregation by channel_id
  GET /admin/ingested-playlists  — ES terms aggregation by playlist_id
  GET /admin/task/{task_id}      — Celery AsyncResult (status + meta + result)

These are NEW endpoints (no deprecated analog) — the deprecated repo
had no YCS UI, so the BFF helpers it would have needed don't exist
upstream. Pure projection / aggregation; no business logic."""
from .router import router


__all__ = ["router"]
