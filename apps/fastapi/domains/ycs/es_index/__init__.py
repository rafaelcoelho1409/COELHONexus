"""ycs/es_index — async bulk-index helpers for ES metadata + transcripts.
Used by Wave 4 Celery tasks (`extract/task.py`) after yt-dlp + Playwright
extraction. Targets `infra/elasticsearch.params.INDEX_METADATA` /
`INDEX_TRANSCRIPTIONS` by default."""
from __future__ import annotations

from . import params, service
