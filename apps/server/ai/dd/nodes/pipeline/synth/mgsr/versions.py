"""mgsr — schema + prompt cache-invalidation markers."""
from __future__ import annotations


MGSR_SCHEMA_VERSION = "2.0"
# v4 (2026-11-06): the replan is real again, with a consumer: a chapter_reviewer agent reads the failing sections and names the ones to write again (and what to
# fix in each); sawc_write rewrites only those on the next iteration. Until v3 (issue #19, 2026-09-07) the structural-action replan was skipped because its output
# had no consumer.
MGSR_PROMPT_VERSION = "v4-reviewer-agent-2026-11-06"
