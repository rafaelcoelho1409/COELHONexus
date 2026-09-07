"""book_harmonize cache-invalidation version markers."""
from __future__ import annotations


BOOK_HARMONIZE_SCHEMA_VERSION = "1.0"
# v3: extract/canonicalize/detect all fall back to json_repair on a bare
# json.loads failure, and the previously-silent "no JSON found" branch now
# logs a warning (issue #15, 2026-09-06) — a single malformed response no
# longer silently zeroes out a chapter's contribution with no trace.
# v4: replaced the greedy whole-response `\{.*\}` DOTALL regex with a
# brace-depth-aware first-balanced-object extractor (issue #15 follow-up,
# 2026-09-06 — confirmed live on the 4th study run: a 4,692-char ch-10
# response defeated both json.loads and json_repair under the old greedy
# extraction). Also added a raw-response-prefix log on every failure and
# a warning when parsed JSON is missing both "claims" and "terms" keys.
# v5 (issue #16, 2026-09-06/07 — confirmed live on the 5th study run):
# two distinct new root causes diagnosed via v4's own raw-response
# logging. (a) A reasoning-preamble-only response with no JSON at all —
# EXTRACT_MAX_TOKENS raised 1000 -> 2000, and all three JSON-returning
# prompts (extract/canonicalize/detect) now explicitly instruct "output
# ONLY the JSON, no preamble/reasoning." (b) A duplicated leading brace
# (`{\n{"claims": [...`) that defeats a brace scan starting only from
# the first '{' — `_all_balanced_json_candidates` now retries from each
# successive '{' (bounded) so the real object right after a stray one is
# still recovered.
BOOK_HARMONIZE_PROMPT_VERSION = "v5-preamble-suppress-multi-brace-2026-09-07"
