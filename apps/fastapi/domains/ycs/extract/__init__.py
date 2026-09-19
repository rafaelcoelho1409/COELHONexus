"""ycs/extract — yt-dlp metadata extraction (deprecated `YtDlpExtractor`).

Sibling of `domains/ycs/content/` which exposes the sync `search` path
on the same `YtDlpExtractor`. This module surfaces the other four
extraction methods (`extract_video`, `extract_batch`, `extract_playlist`,
`extract_channel`).

Per `docs/CODE-CONVENTIONS.md` §4: `domain.py` is pure (argv builders +
URL/ID normalization + result projection); `service.py` is the async
shell around `asyncio.create_subprocess_exec(...)`. Persistence is NOT
done here — Wave 4 Celery tasks wrap these calls + write Elasticsearch.

`task.py` is excluded from this eager chain (§8 Exception 1 —
`from infra.celery import app` at module level); reach it via a direct
`from domains.ycs.extract.task import extract_videos`."""
from __future__ import annotations

from . import domain, errors, params, patterns, schemas, service
