from __future__ import annotations


STEP = "chapter_design"
TARGET_MINUTES = 25.0
MAX_MINUTES = 45.0
# Chapters have a real navigation and setup cost. Treat the suggested count as
# a soft prior: over-fragmentation carries a larger score cost than modest
# under-fragmentation, while very long chapters are penalized by their excess
# reading burden below.
TOO_MANY_CHAPTERS_WEIGHT = 0.18
TOO_FEW_CHAPTERS_WEIGHT = 0.06
OVER_BUDGET_WEIGHT = 0.12
CHAPTER_COUNT_REVIEW_RATIO = 2.0
# The designer can see a broad range of chapter counts. This limits alternatives
# in its prompt, not the number of chapters it may return.
MAX_PARTITION_OPTIONS = 8
EMBED_BATCH = 64
URL_SIMILARITY_BONUS = 0.12
REFERENCE_GROUP_PAGES = 18
ATTEMPTS = 2
RETRY_WAIT_S = 20.0
SETTLE_DELAY_S = 100.0
