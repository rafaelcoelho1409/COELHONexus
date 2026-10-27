"""Tier 2 — tunable scalars."""
from __future__ import annotations


USER_AGENT = "COELHONexus-DocsDistiller-Tier2/1.0"
TIMEOUT_S = 30.0
CONCURRENCY = 8
MIN_OK_BYTES = 200
# Tier-2-gated stub floor: pages below this never reach the manifest (nav
# fragments / course scaffolding) — unless they carry a code example (a short
# example script is a page; see post.domain.worth_keeping). Tier 1 keeps its
# own 300B post-split floor (post.params.SPLIT_MIN_SECTION_BYTES) — this
# constant must NOT be reused there.
STUB_MIN_BYTES = 1024

# An llms.txt is the site's own list of its pages — but some are curated agent indexes, not the docs (Qdrant's lists 88
# of the 589 pages its sitemap has; it used to list ~700). When the index names fewer than this share of the sitemap's
# pages, the sitemap is the better source and the run falls through to it.
COVERAGE_MIN_RATIO = 0.25
# …judged only on a site worth judging: a sitemap this small proves nothing (FastHTML's has 41 pages)…
COVERAGE_MIN_SITEMAP_PAGES = 100
# …and not on one this large, which is every version and language of the docs at once (Streamlit's has 5000) and is not
# something to crawl in place of a 344-page index.
COVERAGE_MAX_SITEMAP_PAGES = 1500
# When the index it hands over is made of markdown files (at least this share of its links end in `.md`), the site
# publishes a markdown twin of its pages, and the sitemap's pages are asked for as markdown first.
MARKDOWN_INDEX_MIN_SHARE = 0.75
# The sitemap is only being counted: one try, a short timeout, like the navigation index in Tier 1.
COVERAGE_TIMEOUT_S = 20.0
