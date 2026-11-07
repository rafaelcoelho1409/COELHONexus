"""Limits for the isolated report-to-PDF renderer."""
from __future__ import annotations

MAX_HTML_BYTES = 24 * 1024 * 1024
MAX_PDF_BYTES = 80 * 1024 * 1024
CONNECT_TIMEOUT_MS = 20_000
RENDER_TIMEOUT_MS = 60_000
