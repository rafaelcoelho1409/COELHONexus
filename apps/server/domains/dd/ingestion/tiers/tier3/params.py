"""Tier 3 — tunable scalars."""
from __future__ import annotations


USER_AGENT = "COELHONexus-DocsDistiller-Tier3/1.0"
TIMEOUT_S = 30.0
CONCURRENCY = 8
MIN_OK_BYTES = 200

# A site that publishes a markdown twin for its pages (Mintlify: `/page.md`; Starlight sites like Terragrunt's) is asked for
# them: the twin is the author's own markdown — tabs, callouts and code blocks whole, no page furniture — where the scraped
# HTML kept about 90% of the prose and added the site's feedback bar. Whether a site publishes them is found by asking this
# many pages, spread over the sitemap, for theirs; when at least TWIN_PROBE_MIN_OK answer, every page is asked first (and a
# page with no twin is still scraped).
TWIN_PROBE_SAMPLE = 4
TWIN_PROBE_MIN_OK = 3

# A page's slug is cut to this many characters (the file name it is stored under).
SLUG_MAX_CHARS = 80

# Nested sitemap depth limit (sitemap index → sitemap → …). Three levels is
# the deepest anyone in the wild has been observed using.
INDEX_MAX_DEPTH = 3
