"""Tier 2 — tunable scalars."""
from __future__ import annotations


TIER_KIND = "llms_txt"  # what this tier is called in the catalog, in progress records and on a page's manifest entry
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

CONNECT_TIMEOUT_S = 10.0

# The index's `##` heading is kept at this length as a page's section, and its blockquote summary as the run's.
SECTION_MAX_CHARS = 120
SUMMARY_MAX_CHARS = 2000

# A link to another host is kept only when it is raw content, not an HTML page (FastHTML curates an HTMX `reference.md`, Starlette
# `.md` guides, `.py` examples and a MonsterUI `apilist.txt` off-host; GitHub repo and PyPI project links are meta-links and go).
CROSS_HOST_SUFFIXES = (".md", ".txt", ".py")

# A page's slug is its name, at most this long, or this when it has none.
SLUG_MAX_CHARS = 80
SLUG_FALLBACK = "page"

# A fetched page smaller than this and with no code fence may be a pointer elsewhere (see domain.looks_like_redirect_stub).
REDIRECT_STUB_MAX_BYTES = 2048

# A page that opens with a filename-shaped H1 (a gist or raw mirror's display name) loses it when a real heading follows within
# this many non-empty lines; a `---` / `...` left directly behind the removed line goes with it.
FILENAME_H1_LOOKAHEAD_LINES = 6
FILENAME_H1_SEPARATORS = ("---", "...")

# A page is markdown when the server says so, or when its URL ends this way; a `.txt` (an `apilist.txt`) is kept as it is, and a
# `.py` (an example script linked from the index) is fenced as Python.
MARKDOWN_CONTENT_TYPES = ("text/markdown", "text/x-markdown")
MARKDOWN_SUFFIX = ".md"
TEXT_SUFFIX = ".txt"
PYTHON_SUFFIX = ".py"

# When two pages of the index would take the same slug, the second takes its nearest URL folder that tells it apart; with no such
# folder, the first label of its host; with none of that, this.
SLUG_STEM_FALLBACK = "x"
