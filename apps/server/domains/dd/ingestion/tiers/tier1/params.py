"""Tier 1 — tunable scalars."""
from __future__ import annotations


TIER_KIND = "llms_full"  # what this tier is called in the catalog, in progress records and on a page's manifest entry
USER_AGENT = "COELHONexus-DocsDistiller-Tier1/1.0"
TIMEOUT_S = 30.0
CONNECT_TIMEOUT_S = 10.0
MIN_OK_BYTES = 500

# What the run keeps of what it fetched (`capture`): the bundle as the site sent it, and the site's own page index beside it.
# A paginated bundle (GitBook: Unsloth's is 2 parts of 100 pages) is kept part by part: `bundle.md`, `bundle.part2.md`, …
BUNDLE_FILE = "bundle.md"
PART_FILE = "bundle.part{n}.md"
NAV_FILE = "llms.txt"

# A bundle that says it has a next part is followed through its parts, at most this many (a loop or a runaway site ends here), and
# only to the same host. The banner is looked for in the first characters of a part.
MAX_PARTS = 25
PART_BANNER_SNIFF_CHARS = 1500

# The bundle's page is named after its host (`code-claude-com-llms-full`), at most this long.
HOST_SLUG_SUFFIX = "-llms-full"
SLUG_MAX_CHARS = 120

# A bundle is fetched in one request, so a flaky moment must not sink the run: these are retried with
# jittered exponential backoff, or for as long as the server's Retry-After asks (capped).
RETRY_ATTEMPTS = 3
RETRY_WAIT_INITIAL_S = 1.0
RETRY_WAIT_MAX_S = 15.0
RETRY_AFTER_MAX_S = 30.0

# The site's own page order (its llms.txt) is a nicety, never a reason to hold a run up: one try, a short timeout.
NAV_TIMEOUT_S = 15.0

# Heuristics for detecting a llms.txt-style link index disguised as
# llms-full.txt (the bundle should have many fenced code blocks; a manifest
# has many URL: pointers and few/no fences).
MANIFEST_MIN_URL_LINES = 100
MANIFEST_MAX_FENCES = 5

# How much of the body is looked at to tell an HTML page (a soft 404, a login wall, a CDN challenge) from markdown.
HTML_SNIFF_CHARS = 400
