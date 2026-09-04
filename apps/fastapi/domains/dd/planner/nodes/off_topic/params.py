"""off_topic — LLM judge tuning + negative-anchor descriptor."""
from __future__ import annotations


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

JUDGE_MAX_TOKENS = 8        # plenty for "KEEP" or "DROP" plus whitespace
# Concurrency: 24 parallel in-flight. Legacy 5 was sized for old LiteLLM bandit
# cascade (Redis + top-K 5). coelho-llm-rotator's pooled http2 (200/100) +
# simple-shuffle + allowed_fails absorbs 429s rotator-side, so bottleneck is
# purely client semaphore. 20 ≈ full utilisation in early tests; 24 saturates
# pool without the old 36% blowup and matches doc_distill 24 for uniform sizing.
# SOTA Sept 2026: Baseten/Decodo 200/100 pool break-even ~200 concurrent.
JUDGE_CONCURRENCY = 24
# Per-call retry budget — retries the pooled rotator call (rotator already
# cascades 40). Jittered backoff avoids herd on shared arms.
JUDGE_MAX_ATTEMPTS = 2
JUDGE_BACKOFF_BASE = 1.5
JUDGE_TIMEOUT_S = 15.0      # KEEP/DROP is 1 token; 15s covers cold TTFT, faster failover than 30s

# Stable meta-content descriptor for the LLM judge (CoC, changelogs, issue templates, etc. that bypass URL filters).
NEGATIVE_DESCRIPTOR = (
    "Repository meta-content: code of conduct, contributing guidelines, "
    "sponsor lists, conference talk archives, GitHub issue templates, "
    "changelog dumps, release notes, generated index pages with no real "
    "teaching content, license text, governance policies, blog posts."
)
