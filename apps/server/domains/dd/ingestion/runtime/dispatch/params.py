"""Dispatch — tunable scalars + tier-source priority order."""
from __future__ import annotations


# Best-source order in catalog entries (highest priority first).
KIND_PRIORITY: tuple[str, ...] = (
    "llms_full",
    "llms_txt",
    "sitemap",
    "docs",
    "github",
)

# What each tier is handed from the catalog entry besides its URL, the slug, the progress and the store:
# (argument, catalog field, the source kinds that take it).
TIER_ARGUMENTS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("framework_name", "name",          ("sitemap", "docs")),
    # What the docs are written in, for a code block whose markup names no language.
    ("code_language",  "code_language", ("llms_txt", "sitemap", "docs")),
    ("path_filter",    "path_filter",   ("llms_full", "llms_txt", "sitemap", "docs")),
    # The site's own page order, for the split.
    ("nav_url",        "llms_txt",      ("llms_full",)),
    # To tell a curated index from the docs.
    ("sitemap_url",    "sitemap",       ("llms_txt",)),
)

# How a run is named in Langfuse (the session) and in Tempo (the span), and what its spans are tagged as.
LANGFUSE_SESSION_NAME = "dd-ingestion"
SPAN_NAME = "dd.ingestion.run"
RUN_KIND = "ingestion"

# Shown for a run whose result carries no tier or outcome (its metric label).
UNKNOWN = "unknown"

REDIS_OP_TIMEOUT_S = 10.0  # ingestion runs wait up to 10s per Redis call (infra.redis default is 5s)

# Watcher poll interval — cooperative `raise_if_cancelled()` doesn't fire
# during Crawl4AI `arun_many` (30-60s blocking await).
CANCEL_POLL_S = 1.0

# Settle gap between cleanup passes so in-flight parallel writes can finish
# (tier 2/3/4a stream writes in coroutines that may unwind after cancel).
CLEANUP_SETTLE_S = 0.8
