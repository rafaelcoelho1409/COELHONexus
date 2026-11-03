from __future__ import annotations


# The Celery task that extracts one book: it is warned after SOFT_TIME_LIMIT_S and killed after TIME_LIMIT_S. Extraction runs on CPU at
# a few seconds a page, so a 600-page book takes the better part of an hour; the limit leaves room for a slow machine and the models'
# first download. The book's lock is a short lease the run keeps renewing (`runtime.progress.params.LOCK_LEASE_S`), so a killed task
# frees the book within one lease.
TASK_NAME = "domains.bd.ingestion.task.run_extraction"
SOFT_TIME_LIMIT_S = 10800
TIME_LIMIT_S = 10860

# What a book may be. A page key is zero-padded to four digits, so MAX_PAGES stays under 10000.
MAX_UPLOAD_BYTES = 300 * 1024 * 1024
MAX_PAGES = 3000

# Pages converted per extraction call. The progress the user sees moves one slice at a time, and a cancel lands between slices; a
# bigger slice is a little faster per page (one layout batch) and a lot less responsive.
SLICE_PAGES = 5
