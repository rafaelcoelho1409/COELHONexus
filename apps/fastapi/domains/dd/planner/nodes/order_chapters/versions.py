"""order_chapters prompt version — bumped when prompt or scoring policy
changes. Cache invalidates cleanly."""
from __future__ import annotations


# v3 (2026-09-08): timeout_s raised 60s -> 120s, matching Synth's own
# percentile-proven p99/max range on this shared Rotator pool. Confirmed
# live: two consecutive fastapi runs both saw all 3 samples fail with
# RouterRateLimitError "No deployments available" and fall back to
# identity ordering.
# v4 (2026-09-08): timeout_s corrected 120s -> 70s using this node's OWN
# 14-day Langfuse percentiles (p99=50.3s, max=54.9s) instead of Synth's
# borrowed numbers — 120s never fixed this node's 100% failure rate, and
# its own history shows genuine successes never need anywhere near 120s.
# v5 (2026-09-09): SETTLE_DELAY_S corrected 20s -> 130s — same fix as
# doc_distill/chapter_assign; 20s was far shorter than the Rotator's own
# cooldown_time=120 and never reliably outlasted chapter_assign's tail-end
# cooldowns.
# v6 (2026-10-13): the docs' own navigation order votes in the Borda aggregation
# (params.SOURCE_ORDER_WEIGHT extra rankings) and is the fallback order when every
# LLM sample fails; the Foundations chapter (role == "foundations") is pinned first.
# v7 (2026-10-13): the foundational-title rule skips Foundations/appendix/gap chapters
# and no longer matches "cli" (fastmcp: "FastMCP CLI Tooling" and a gap chapter named
# `…-quickstart` were both pinned to the front); the generic appendix chapter closes the book.
# v8 (2026-10-13): source-order votes only when the manifest order is the docs' own sequence
# (not a single-URL dump or an alphabetical sitemap); the prompt shows each chapter's page count
# and API/reference share and puts reference/operations chapters after core usage.
# v9 (2026-10-13): the model returns chapter TITLES in reading order (not position numbers), each
# sample sees the chapters in its own shuffled arrangement, rationale first, 5-stage rubric;
# migration / upgrade / changelog chapters are pinned ahead of the appendix. Live test on the
# round-4 plans: per-sample constraint satisfaction 11.4/12, 10.8/13, 26.2/28 vs 10.0, 8.4, 26.4.
PROMPT_VERSION = "v9-title-order-2026-10-13"
