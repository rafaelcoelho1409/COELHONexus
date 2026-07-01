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
# Concurrency: 5 parallel in-flight calls — the ParetoBandit + LiteLLM
# cascade handles transient failures; the inner helper already routes each
# the bandit's top-K list, so the outer concurrency stays modest.
JUDGE_CONCURRENCY = 5
# Per-call retry budget — outer wrapper retries the WHOLE bandit cascade
# this many times if it raises (covers transient infra failures like Redis
# blips). Each bandit cascade itself tries top-K=5 deployments internally.
JUDGE_MAX_ATTEMPTS = 2
JUDGE_BACKOFF_BASE = 1.5

# Stable meta-content descriptor for the LLM judge (CoC, changelogs, issue templates, etc. that bypass URL filters).
NEGATIVE_DESCRIPTOR = (
    "Repository meta-content: code of conduct, contributing guidelines, "
    "sponsor lists, conference talk archives, GitHub issue templates, "
    "changelog dumps, release notes, generated index pages with no real "
    "teaching content, license text, governance policies, blog posts."
)
