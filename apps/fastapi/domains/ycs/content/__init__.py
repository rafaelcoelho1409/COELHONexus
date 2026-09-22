"""ycs/content — yt-dlp subprocess search + filter-grammar translation.

Slice 1 walking skeleton: synchronous `POST /api/v1/ycs/content/search`,
zero storage, zero LLM, zero Celery. Validates the conventions split
(`domain.py` pure + `service.py` I/O), the BFF reverse-proxy round-trip,
and the yt-dlp + bgutil-PoT sidecar wiring in Dockerfile.fastapi."""
from __future__ import annotations
from . import domain, errors, params, patterns, schemas, service
