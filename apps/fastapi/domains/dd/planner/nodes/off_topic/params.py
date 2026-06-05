"""off_topic — LLM judge tuning + negative-anchor descriptor."""
from __future__ import annotations


# LLM judge config (2026-05-25 — head+tail truncation per Chroma 2025
# Context Rot study + Lost-in-the-Middle ICLR 2025). The KEEP/DROP signal
# concentrates BOTH at page heads (TOC indicators like 'Code of Conduct',
# 'Sponsors') AND at page tails (license blocks, 'Edit on GitHub' links,
# changelog dumps). Head-only truncation systematically misses the tail
# signals; head+tail at the SAME token cost catches ~+2-4 F1 points on
# borderline pages without enlarging the input window past the smallest-
# context model in our bandit pool (8K-window members would 4xx).
#
# Sweet spot per research:
#   - 1.5K-4K tokens / 6-16K chars TOTAL — past that, Context Rot kicks
#     in and accuracy DEGRADES (15-85% drops measured across 18 models)
#   - head:tail ratio ~2:1 — head wins on most DROP signals, tail catches
#     license/footer DROPs
JUDGE_HEAD_CHARS = 3000     # leading chars (covers TOC + first paragraph)
JUDGE_TAIL_CHARS = 1500     # trailing chars (covers license/footer/edit-links)
JUDGE_HEAD_TAIL_SEP = "\n\n[…]\n\n"
# Bypass head+tail entirely when the page is short enough to fit in both
# windows combined — sending the WHOLE small page is strictly better than
# fake-truncating with a "[…]" gap in the middle.
JUDGE_BODY_MIN_FOR_SPLIT = (
    JUDGE_HEAD_CHARS + JUDGE_TAIL_CHARS + len(JUDGE_HEAD_TAIL_SEP)
)

JUDGE_MAX_TOKENS = 8        # plenty for "KEEP" or "DROP" plus whitespace
# Concurrency: 5 parallel in-flight calls — the ParetoBandit + LiteLLM
# cascade handles transient failures; the inner helper already routes each
# call through the best-ranked deployment with per-attempt retries down
# the bandit's top-K list, so the outer concurrency stays modest.
JUDGE_CONCURRENCY = 5
# Per-call retry budget — outer wrapper retries the WHOLE bandit cascade
# this many times if it raises (covers transient infra failures like Redis
# blips). Each bandit cascade itself tries top-K=5 deployments internally.
JUDGE_MAX_ATTEMPTS = 2
JUDGE_BACKOFF_BASE = 1.5

# Negative-anchor template. Stable, framework-independent — describes the
# kind of "looks like docs but isn't" content that bypasses URL filters
# (CoC, sponsor lists, conference talk archives, issue templates,
# changelog dumps, generated index pages).
NEGATIVE_DESCRIPTOR = (
    "Repository meta-content: code of conduct, contributing guidelines, "
    "sponsor lists, conference talk archives, GitHub issue templates, "
    "changelog dumps, release notes, generated index pages with no real "
    "teaching content, license text, governance policies, blog posts."
)
