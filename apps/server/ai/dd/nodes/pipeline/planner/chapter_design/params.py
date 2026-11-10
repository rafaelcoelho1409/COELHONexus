from __future__ import annotations


STEP = "chapter_design"
TARGET_MINUTES = 25.0
MAX_MINUTES = 45.0
# Chapters have a real navigation and setup cost. The suggested count stays a
# soft prior, while each extra chapter must earn its overhead through a distinct
# objective or safer lesson size. There is no hard count ceiling. Very long
# chapters are penalized for their excess reading burden below.
TOO_MANY_CHAPTERS_WEIGHT = 0.50
TOO_FEW_CHAPTERS_WEIGHT = 0.06
OVER_BUDGET_WEIGHT = 0.20
CHAPTER_COUNT_REVIEW_RATIO = 2.0
# The designer can see a broad range of chapter counts. This limits alternatives
# in its prompt, not the number of chapters it may return.
MAX_PARTITION_OPTIONS = 4
# Don't ask the architect to choose a partition whose average lesson size is
# not the problem: extremely oversized candidates cannot represent a study plan.
# This is a filter on candidate quality, not a chapter-count ceiling.
MAX_CANDIDATE_CHAPTER_OVER_BUDGET_FACTOR = 3.0
# The architect gets two targeted repair rounds after its initial structured answer.
DESIGN_REPAIR_ROUNDS = 2
EMBED_BATCH = 64
URL_SIMILARITY_BONUS = 0.12
REFERENCE_GROUP_PAGES = 18
ATTEMPTS = 1
RETRY_WAIT_S = 20.0
SETTLE_DELAY_S = 100.0
