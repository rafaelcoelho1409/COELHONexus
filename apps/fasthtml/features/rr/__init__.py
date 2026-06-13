"""Research Radar — feature package (step 5b 2026-06-12).

Pattern mirrors features/dd/ + features/ycs/:
  - `routes.py` registers the route(s)
  - `body.py` composes the page body via FastHTML's Python DSL

The page calls into FastAPI via the proxy:
  POST /api/v1/rr/scan          → enqueue scan; returns {scan_id, task_id, ...}
  GET  /api/v1/rr/scan/{id}     → snapshot status + findings (when done)
  GET  /api/v1/rr/scan/{id}/events  → SSE phase stream

Static JS lives at static/js/rr/main.js.
"""
from .routes import register


__all__ = ["register"]
