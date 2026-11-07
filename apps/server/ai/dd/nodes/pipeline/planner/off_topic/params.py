"""off_topic — LLM judge tuning + negative-anchor descriptor."""
from __future__ import annotations


# The name this stage reports its progress events, cached items and scratch data under.
STEP = "off_topic"


# Head+tail truncation (Chroma 2025 Context Rot + ICLR 2025 Lost-in-the-Middle): +2-4 F1 vs head-only; 2:1 ratio stays within 8K-window models.
JUDGE_HEAD_CHARS = 3000     # leading chars (covers TOC + first paragraph)
JUDGE_TAIL_CHARS = 1500     # trailing chars (covers license/footer/edit-links)
JUDGE_HEAD_TAIL_SEP = "\n\n[…]\n\n"
# Bypass head+tail entirely when the page is short enough to fit in both
# windows combined — sending the WHOLE small page is strictly better than
# fake-truncating with a "[…]" gap in the middle.
JUDGE_BODY_MIN_FOR_SPLIT = (
    JUDGE_HEAD_CHARS + JUDGE_TAIL_CHARS + len(JUDGE_HEAD_TAIL_SEP)
)

JUDGE_MAX_TOKENS = 700     # not just "KEEP"/"DROP": the rotator's general pool
# includes reasoning-tuned models (gpt-oss, deepseek-v4) that emit a <think>
# block before the verdict. At 8 tokens that block alone eats the whole
# budget and the response comes back empty — confirmed from a real run
# (129/144 judge errors, all unparseable_verdict cases had raw=''). 300 was
# the first fix; raised to 400 after unparseable_verdict kept climbing
# (2→5→11→15 across consecutive runs) as other providers' cooldowns
# concentrated traffic onto fewer, more reasoning-heavy deployments — a
# ceiling costs nothing for models that finish early, so widening it is
# free insurance. judge_one() also escalates further (+200, temp 0.4) on
# the in-node retry specifically for this failure mode.
# 2026-09-27: 400 -> 700, same free-insurance logic — no new failure spike,
# just closing the gap before this node needs its own emergency bump like
# chapter_propose/doc_distill did.
# Concurrency: 16 parallel (was 24). 24×144 burst → 49 timeouts (88s) on
# free-tier general; 16 cuts burst ~33% and jitter 1.3× avoids herd.
# SOTA: Baseten/Decodo 200/100 pool handles 16×8tok easily, 24 saturated.
JUDGE_CONCURRENCY = 16
# Per-call retry budget — retries the pooled rotator call (rotator already
# cascades 40). Jittered backoff avoids herd on shared arms.
JUDGE_MAX_ATTEMPTS = 2
# Linear wait between the asks of one page (`agents.service.ask` waits this times the attempt number).
JUDGE_RETRY_WAIT_S = 4.0
# Progress events per run, at most (the web shows `judged / total`).
PROGRESS_EVENTS = 40
# Stable meta-content descriptor for the LLM judge (CoC, changelogs, issue templates, etc. that bypass URL filters).
NEGATIVE_DESCRIPTOR = (
    "Repository meta-content: code of conduct, contributing guidelines, "
    "sponsor lists, conference talk archives, GitHub issue templates, "
    "changelog dumps, release notes, generated index pages with no real "
    "teaching content, license text, governance policies, blog posts."
)

# API-reference pages are on-topic by construction: virtual sub-pages (one per
# documented class/function/section, split out of a framework's own reference
# page) are never sent to the relevance judge. A parent page must yield at least
# this many such sub-pages to count as a reference page. Measured: every doc the
# judge dropped on elasticsearch-python (7: Exceptions & Warnings, Autoscaling)
# and asyncio (1: asyncio.wrap_future) was one of these.
REFERENCE_MIN_SIBLINGS = 4

# A DROP verdict on a page at least this long (judge-input chars) needs a second DROP from another
# call before the page leaves the corpus; shorter pages (stubs, redirects) keep the single verdict.
RECHECK_MIN_CHARS = 2000
