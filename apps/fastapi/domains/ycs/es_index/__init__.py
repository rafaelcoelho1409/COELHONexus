"""ycs/es_index — async bulk-index helpers for ES metadata + transcripts.
Used by Wave 4 Celery tasks (`extract/task.py`) after yt-dlp + Playwright
extraction. Targets `infra/elasticsearch.params.INDEX_METADATA` /
`INDEX_TRANSCRIPTIONS` by default."""
from .params import BULK_REFRESH, INDEXED_STATUSES
from .service import (
    delete_videos_from_es,
    index_transcriptions_to_elasticsearch,
    index_videos_to_elasticsearch,
)


__all__ = [
    "BULK_REFRESH",
    "INDEXED_STATUSES",
    "delete_videos_from_es",
    "index_transcriptions_to_elasticsearch",
    "index_videos_to_elasticsearch",
]
