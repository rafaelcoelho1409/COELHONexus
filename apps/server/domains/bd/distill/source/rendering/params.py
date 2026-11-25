"""Limits for report rendering and PDF artifacts."""
from __future__ import annotations

HTML_TYPE = "text/html; charset=utf-8"
PDF_TYPE = "application/pdf"
MAX_PDF_BYTES = 80 * 1024 * 1024
MAX_INLINE_SOURCE_FIGURE_BYTES = 2 * 1024 * 1024
MAX_INLINE_SOURCE_FIGURE_TOTAL_BYTES = 8 * 1024 * 1024
