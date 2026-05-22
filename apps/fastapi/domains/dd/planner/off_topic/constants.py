from __future__ import annotations


# LLM judge config
_JUDGE_BODY_CHARS = 4000     # chars sent to the LLM per page
_JUDGE_MAX_TOKENS = 8        # plenty for "KEEP" or "DROP" plus whitespace
# Concurrency: 5 parallel in-flight calls — the ParetoBandit + LiteLLM
# cascade handles transient failures; the inner helper already routes
# each call through the best-ranked deployment with per-attempt retries
# down the bandit's top-K list, so the outer concurrency stays modest.
_JUDGE_CONCURRENCY = 5
# Per-call retry budget — outer wrapper retries the WHOLE bandit cascade
# this many times if it raises (covers transient infra failures like Redis
# blips). Each bandit cascade itself tries top-K=5 deployments internally.
_JUDGE_MAX_ATTEMPTS = 2
_JUDGE_BACKOFF_BASE = 1.5

# Negative-anchor template. Stable, framework-independent — describes
# the kind of "looks like docs but isn't" content that bypasses URL
# filters (CoC, sponsor lists, conference talk archives, issue
# templates, changelog dumps, generated index pages).
_NEGATIVE_DESCRIPTOR = (
    "Repository meta-content: code of conduct, contributing guidelines, "
    "sponsor lists, conference talk archives, GitHub issue templates, "
    "changelog dumps, release notes, generated index pages with no real "
    "teaching content, license text, governance policies, blog posts."
)
