"""Tier 1 — tunable scalars."""
from __future__ import annotations


USER_AGENT = "COELHONexus-DocsDistiller-Tier1/1.0"
TIMEOUT_S = 30.0
MIN_OK_BYTES = 500

# A bundle is fetched in one request, so a flaky moment must not sink the run: these are retried with
# jittered exponential backoff, or for as long as the server's Retry-After asks (capped).
RETRY_ATTEMPTS = 3
RETRY_WAIT_INITIAL_S = 1.0
RETRY_WAIT_MAX_S = 15.0
RETRY_AFTER_MAX_S = 30.0
TRANSIENT_STATUSES = frozenset({429, 500, 502, 503, 504})

# The site's own page order (its llms.txt) is a nicety, never a reason to hold a run up: one try, a short timeout.
NAV_TIMEOUT_S = 15.0

# Heuristics for detecting a llms.txt-style link index disguised as
# llms-full.txt (the bundle should have many fenced code blocks; a manifest
# has many URL: pointers and few/no fences).
MANIFEST_MIN_URL_LINES = 100
MANIFEST_MAX_FENCES = 5

# How much of the body is looked at to tell an HTML page (a soft 404, a login wall, a CDN challenge) from markdown.
HTML_SNIFF_CHARS = 400
