"""api — env reads for the HTTP edge.

Env vars:
  CORS_ORIGINS   — comma-separated browser origins allowed by CORSMiddleware
                   (default `*`, the behaviour before this was configurable)"""
from __future__ import annotations
from . import domain

import os


CORS_ORIGINS = domain.parse_csv(os.environ.get("CORS_ORIGINS", "*"))
