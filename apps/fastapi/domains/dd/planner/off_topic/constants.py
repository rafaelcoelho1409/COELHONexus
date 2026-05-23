from __future__ import annotations


# ─── Cross-encoder rerank fast-path (Phase A, 2026-05-23) ─────────────────────
# When KD_OFF_TOPIC_USE_RERANK=1, off_topic skips the per-doc LLM judge entirely
# and instead batches all candidates through `nvidia/llama-nemotron-rerank-1b-v2`
# (the same NIM rerank model already used by `dd-rerank`). The cross-encoder
# scores `(query=framework_descriptor, passage=doc)` pairs and returns logits;
# sigmoid + threshold yields a calibrated KEEP/DROP verdict per doc.
#
# Expected speedup at LangChain scale: 280 s LLM-judge → ~15-25 s rerank.
# Quality caveat: threshold MUST be calibrated on a ~50-100 doc validation set
# per framework family. Default 0.35 is the research-recommended starting point
# (Brenndoerfer 2026 cross-encoder calibration guide) but should be tuned for
# >95% recall on hand-labeled validation data before flipping to production.
_RERANK_THRESHOLD = 0.35
_RERANK_BATCH_SIZE = 256   # NIM nemotron-rerank-1b-v2 hard cap is 512; 256 keeps
                            # per-call latency stable + leaves headroom
_RERANK_DOC_CHARS = 6000   # truncate each passage to ~6000 chars (well under the
                            # 8192-token nemotron-rerank context, leaves headroom)

# LLM judge config (legacy path; used when KD_OFF_TOPIC_USE_RERANK is unset)
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
