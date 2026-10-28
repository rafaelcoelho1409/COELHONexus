from __future__ import annotations


# Below this size the monolith path no-ops (idempotent for pre-split corpora).
MONOLITH_SPLIT_THRESHOLD_BYTES = 50_000

# Individual pages at/over this size get H2/H3 pre-split in multi-page
# corpora (the monolith path above only fires for single-page corpora, so
# without this Tier 3's giants sailed through dedup untouched). Sized to the
# downstream consumers that motivated it — doc_distill BODY_CHARS_MAX and
# synth digest MAX_SOURCE_CHARS are both 100_000 — so nothing Planner/Synth
# reads ever truncates silently again. Proven splitter, same
# _size_aware_recursive_split the monolith path uses (atomic fences/tables,
# parent-H1 prepended); pages with no clean split come back intact.
OVERSIZED_SPLIT_BYTES = 100_000

# 300 B, not 64 B — micro-fragments leaked through at the lower floor.
SPLIT_MIN_SECTION_BYTES = 300

# Empirical: Dask Changelog (722 KB) splits cleanly, DataFrame/Futures sections
# (104-139 KB, no splittable H2) stay intact. Above ~150 KB → slow rendering;
# below → over-fragments API references.
SPLIT_MAX_SECTION_BYTES = 150_000

SOURCE_MIN_MARKERS = 3

# A monolith cut on its H1s (or its `Source:` lines) into fewer sections than this is a single-H1 bundle (Docusaurus wraps the
# whole thing in one), and goes on to the size-aware H2/H3 split instead.
MONOLITH_MIN_H1_SECTIONS = 3

# The heading levels an oversized section is cut on, in order: H2 first, H3 where H2 alone does not bring the pieces under the
# cap. Not H4: see domain.split_oversized_page.
SUBSPLIT_LEVELS = (2, 3)

# The `---` that separated a section from the next one is looked for in this many trailing characters only.
TRAILING_RULE_TAIL_CHARS = 400

# How a document is read into blocks, so a code fence, a table or a block of HTML stays one piece (markdown-it-py).
MARKDOWN_PRESET = "commonmark"
MARKDOWN_OPTIONS = {"html": True}
MARKDOWN_ENABLED_RULES = ("table", "strikethrough")

# A site's own index (`llms.txt`) lists its pages in navigation order; the bundle next to it is often alphabetical by
# URL. Split pages follow the index only when it accounts for at least this share of them — below that it is some other
# index (Streamlit's names none of its bundle's pages) and the bundle's order stands.
NAV_MIN_MATCH_RATIO = 0.5

# The site's own section headings (`## Getting started` in its llms.txt) become the pages' sections, so the explorer
# groups by them — but only when the index really has sections: one heading over every page ("Docs") groups nothing.
NAV_MIN_SECTIONS = 2

# Titles that repeat ("All Parameters" under agent/, browser/, legacy/…) are told apart by the docs section they sit in;
# at most this many parent folders are named.
TITLE_CONTEXT_MAX_SEGMENTS = 3

# The site's name that a `<title>` ends with ("Quick start | Grafana Tempo documentation") is taken off the titles when the same
# trailing segment is on at least this share of them, and at least this many (domain.strip_site_suffix).
TITLE_SUFFIX_SEPARATORS = (" | ", " - ", " – ", " — ", " · ", " :: ")
TITLE_SUFFIX_MIN_SHARE = 0.5
TITLE_SUFFIX_MIN_COUNT = 8

# URL folders that say nothing about a page's topic, so they never name it: the language of the docs, the word "docs", and
# the names a host gives a release (a numbered one is skipped by `patterns.VERSION_SEGMENT_RE`).
TITLE_SKIP_SEGMENTS = frozenset({
    "docs", "doc", "documentation",
    "main", "master", "latest", "stable", "current", "next",
    "en", "fr", "de", "es", "it", "pt", "pt-br", "ja", "jp", "ko", "zh", "zh-cn", "zh-tw", "ru", "id", "tr", "vi", "nl", "pl",
})

# A changelog that the generic heading split cuts into pieces named alike (FastMCP's: 126 "New Contributors" / "What's
# Changed" fragments) stays one page. Kept apart only when at least this share of the pieces has a title of its own
# (Dask's, one section per version).
CHANGELOG_MIN_DISTINCT_RATIO = 0.5

# Relative links are made absolute against the page's own URL — except on hosts where "relative" means something else
# (a raw GitHub file's `/x` is the repository's, not the host's).
LINK_REWRITE_SKIP_HOSTS = ("raw.githubusercontent.com",)

# Lines scanned after a section's H1 looking for a SECOND H1 to use as the
# display title (llms-full.txt generators sometimes wrap the real title in a
# throwaway filename-derived heading). See domain._resolve_display_heading.
HEADING_LOOKAHEAD_LINES = 6

# A page's title is its first H1; only this many leading lines are searched for it, and a sub-split child's own
# heading is looked for in the few lines right after (see domain.display_title).
TITLE_SCAN_LINES = 40
TITLE_CHILD_LOOKAHEAD_LINES = 4
TITLE_MAX_CHARS = 120

# A heading-less section is named by its first sentence of prose (domain._fallback_heading): a line that starts with one of these
# is markup (quote, list, table), not a sentence; one shorter than the minimum is not a name; one longer is cut.
FALLBACK_HEADING_SKIP_LEADS = (">", "-", "*", "+", "|")
FALLBACK_HEADING_MIN_CHARS = 20
FALLBACK_HEADING_MAX_CHARS = 120

# A page's slug is its heading, cut at this length; two pages that still share one get this many characters of their body's hash;
# a section with no heading at all is `section-NNNN`, its number at least this many digits wide.
SLUG_MAX_CHARS = 60
SLUG_HASH_CHARS = 6
SECTION_INDEX_MIN_WIDTH = 4

# A section name taken from the site's index is cut at this length.
NAV_SECTION_MAX_CHARS = 60

# What marks a changelog: a title with the word in it, and the slug a page called so ends with (the release pages replace it).
CHANGELOG_TITLE_WORD = "changelog"
CHANGELOG_SLUG_SUFFIX = "-changelog"

# The tier a changelog-release page is filed under, which corpus_load reads back to keep it out of Planner and Synth.
CHANGELOG_TIER = "changelog"

# A page the split or the dedup writes back is Markdown; one whose parent is gone from the manifest has no tier to carry on.
PAGE_CONTENT_TYPE = "text/markdown"
UNKNOWN_TIER = "unknown"

# The `kind` of the summary `apply_to_store` returns (what the run's progress records).
SUMMARY_KIND_SPLIT = "split"
SUMMARY_KIND_DEDUP = "dedup"

# 32 amortizes latency without overwhelming the MinIO pool (serial was 75s/1500 pages).
READ_CONCURRENCY = 32
DELETE_CONCURRENCY = 32
