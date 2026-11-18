"""Tier 3 — tunable scalars."""
from __future__ import annotations


TIER_KIND = "sitemap"  # what this tier is called in the catalog, in progress records and on a page's manifest entry
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

# A page's slug is cut to this many characters (the file name it is stored under); a page with none to name it is `page`.
SLUG_MAX_CHARS = 80
SLUG_FALLBACK = "page"

# URL folders that name a release: an alias for the current one (kept first when a page exists under several), and the names of
# a development branch (kept last). A numbered folder (`v5.21.0`, `2.0`) is told by `post.patterns.VERSION_SEGMENT_RE`.
CURRENT_ALIASES = ("stable", "latest", "current")
BRANCH_ALIASES = ("main", "master", "next")

# A page's markdown twin replaces its `.html` / `.htm` (or its trailing slash) with `.md`.
MARKDOWN_SUFFIX = ".md"
HTML_SUFFIXES = (".html", ".htm")

CONNECT_TIMEOUT_S = 10.0

# A sitemap is XML (lxml's XML parser), or whatever the standard library's HTML parser can make of it; a page's `<lastmod>` is
# kept to this length ("" when it has none).
SITEMAP_PARSER = "lxml-xml"
FALLBACK_PARSER = "html.parser"
LASTMOD_MAX_CHARS = 32

# When two pages would take the same slug and the URL has no folder to tell them apart, the second takes this, then a counter.
SLUG_STEM_FALLBACK = "x"

# Nested sitemap depth limit (sitemap index → sitemap → …). Three levels is
# the deepest anyone in the wild has been observed using.
INDEX_MAX_DEPTH = 3
