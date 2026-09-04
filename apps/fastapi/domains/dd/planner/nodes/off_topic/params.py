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

JUDGE_MAX_TOKENS = 400     # not just "KEEP"/"DROP": the rotator's general pool
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
# Concurrency: 16 parallel (was 24). 24×144 burst → 49 timeouts (88s) on
# free-tier general; 16 cuts burst ~33% and jitter 1.3× avoids herd.
# SOTA: Baseten/Decodo 200/100 pool handles 16×8tok easily, 24 saturated.
JUDGE_CONCURRENCY = 16
# Per-call retry budget — retries the pooled rotator call (rotator already
# cascades 40). Jittered backoff avoids herd on shared arms.
JUDGE_MAX_ATTEMPTS = 2
JUDGE_BACKOFF_BASE = 1.5
JUDGE_TIMEOUT_S = 45.0      # was 30s for 8tok; 300tok budget needs more decode time headroom too

# Stable meta-content descriptor for the LLM judge (CoC, changelogs, issue templates, etc. that bypass URL filters).
NEGATIVE_DESCRIPTOR = (
    "Repository meta-content: code of conduct, contributing guidelines, "
    "sponsor lists, conference talk archives, GitHub issue templates, "
    "changelog dumps, release notes, generated index pages with no real "
    "teaching content, license text, governance policies, blog posts."
)
