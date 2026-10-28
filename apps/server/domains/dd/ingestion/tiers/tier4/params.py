from __future__ import annotations


TIER_KIND      = "docs"        # what this tier is called in the catalog, in progress records and on a page's manifest entry
BROWSER_TIER   = "playwright"  # what its browser phase is called in progress records and on a page's manifest entry
USER_AGENT     = "COELHONexus-DocsDistiller-Tier4/1.0"
ACCEPT         = "text/html,application/xhtml+xml"
TIMEOUT_S      = 30.0
CONNECT_TIMEOUT_S = 10.0
PROBE_TIMEOUT_S   = 10.0   # the docs-path probe: a HEAD (a GET when the host answers 405) per candidate
CONCURRENCY    = 10
MIN_OK_BYTES   = 200
BFS_MAX_DEPTH  = 3

# A page is fetched up to this many times when the connection times out or fails, waiting a jittered exponential time between.
GET_ATTEMPTS       = 3
GET_WAIT_INITIAL_S = 1
GET_WAIT_MAX_S     = 8

DISCOVERY_MIN_URLS         = 5
PHASE4A_FAIL_RATE_TRIGGER  = 0.5     # >50% Phase 4a failures → escalate to Playwright

DOCS_PROBES: tuple[str, ...] = (
    "/docs/", "/stable/", "/latest/", "/main/",
    "/v1/", "/en/", "/guide/", "/documentation/",
)

SPA_BODY_MIN     = 1500
SPA_TEXT_MIN     = 200
SPA_SAMPLE_SIZE  = 3

# A page's slug is its title's or its path's, at most this long; a page that the Sphinx split cuts into pieces names each
# `{page slug}--{piece}`, at most this long.
SLUG_MAX_CHARS      = 80
SLUG_FALLBACK       = "page"
SUB_SLUG_SEPARATOR  = "--"
SUB_SLUG_MAX_CHARS  = 120

# The seeder (crawl4ai's `AsyncUrlSeeder`): where it looks for a host's URLs, how many it may return, and which of its answers
# are pages.
SEEDER_SOURCE       = "sitemap+cc"
SEEDER_MAX_URLS     = 10_000_000
SEEDER_OK_STATUSES  = ("valid", "found")

# An `<a href>` that is not a link to a page.
NON_PAGE_HREF_PREFIXES = ("#", "javascript:", "mailto:", "tel:", "data:")
HTTP_SCHEMES           = ("http://", "https://")

# The browser phase (Playwright through crawl4ai): what it does not download (Next.js prefetch data, images, fonts, media, CSS —
# only the text is wanted).
BLOCKED_ROUTES: tuple[str, ...] = (
    "**/_next/data/**",
    "**/*.{png,jpg,jpeg,gif,webp,svg,ico,woff,woff2,ttf,otf,mp4,webm}",
    "**/*.css",
)

# crawl4ai's own markdown (the fallback when the rendered HTML gave nothing): pruned to the content by the filter's score.
MD_PRUNING_THRESHOLD      = 0.45
MD_PRUNING_THRESHOLD_TYPE = "dynamic"
MD_PRUNING_MIN_WORDS      = 5

# crawl4ai's dispatcher: it slows down above this share of the memory used, goes on below the recovery one, checks every
# interval, and spaces the requests to a host by a random delay in the range (up to the maximum when the host pushes back).
MEMORY_THRESHOLD_PERCENT    = 85.0
RECOVERY_THRESHOLD_PERCENT  = 75.0
DISPATCH_CHECK_INTERVAL_S   = 1.0
RATE_MAX_RETRIES            = 3
PRIMARY_RATE_DELAY_S        = (0.5, 1.5)
PRIMARY_RATE_MAX_DELAY_S    = 20.0
RETRY_RATE_DELAY_S          = (1.0, 3.0)
RETRY_RATE_MAX_DELAY_S      = 30.0
RETRY_SESSION_PERMIT        = 1

# Each URL gets a session of its own, named with this many characters of a random id.
SESSION_ID_CHARS = 12

# A navigation failure worth one more try with longer timeouts (the page may just have been slow), recognised by its text; a
# failure's message is kept in the progress record to this length.
TRANSIENT_NAV_ERRORS = ("ACS-GOTO", "Timeout", "timeout", "Navigation", "net::ERR")
ERROR_MAX_CHARS      = 300

# What each browser pass waits for, and how it returns: after `domcontentloaded`, until the page is `complete` and has a content
# element, with a short pause for late scripts (a long one on the retry); a page with fewer words than the threshold is dropped.
RUN_WAIT_UNTIL            = "domcontentloaded"
RUN_WAIT_FOR              = "js:() => document.readyState === 'complete' && !!document.querySelector('#__next, main, article')"
RUN_WORD_COUNT_THRESHOLD  = 50
RUN_EXCLUDED_TAGS         = ("nav", "footer", "aside")
RUN_MAX_RETRIES           = 2
PRIMARY_RETURN_DELAY_S    = 0.2
RETRY_RETURN_DELAY_S      = 2.0


# One page at a time. The browser is a shared CDP one, and crawl4ai's concurrent sessions race on it: with 4 sessions, 3 of 8 TypeScript
# handbook pages came back with ANOTHER page's HTML (`classes.html` held "Conditional Types"; 28 of the 34 stored pages were wrong, and
# half of the 80 fetched were duplicates of each other). With one session all 8 were right (measured 2026-10-27, pw_race.py).
MAX_SESSION_PERMIT     = 1
PAGE_TIMEOUT_MS        = 60_000
RETRY_PAGE_TIMEOUT_MS  = 90_000
RETRY_DELAY_S          = 2.0
DEFAULT_MIN_OK_BYTES   = 200
