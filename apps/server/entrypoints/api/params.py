"""api — env reads for the HTTP edge.

Env vars:
  CORS_ORIGINS   — comma-separated browser origins allowed by CORSMiddleware
                   (default `*`, the behaviour before this was configurable)
  ALLOWED_HOSTS  — comma-separated Host values the MCP endpoint accepts"""
from __future__ import annotations
from . import domain

import os


CORS_ORIGINS = domain.parse_csv(os.environ.get("CORS_ORIGINS", "*"))
ALLOWED_HOSTS = domain.parse_csv(os.environ.get("ALLOWED_HOSTS", ""))
