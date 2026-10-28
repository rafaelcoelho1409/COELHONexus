"""Tier 5 — tunable scalars + endpoint constants + path filter lists."""
from __future__ import annotations


TIER_KIND = "github"  # what this tier is called in the catalog, in progress records and on a page's manifest entry
USER_AGENT = "COELHONexus-DocsDistiller-Tier5/1.0"
GITHUB_ACCEPT = "application/vnd.github+json"
TIMEOUT_S = 30.0
CONNECT_TIMEOUT_S = 10.0
CONCURRENCY = 10
MIN_OK_BYTES = 150

API_BASE = "https://api.github.com"
RAW_BASE = "https://raw.githubusercontent.com"

# A GitHub repo URL is on one of these hosts, and its repo name may end in `.git`.
GITHUB_HOSTS = ("github.com", "www.github.com")
GIT_SUFFIX = ".git"

# The branch to read when the repository does not say which is its default.
DEFAULT_BRANCH = "main"

# The GitHub API and the raw-file CDN are retried on a timeout or network error: this many times, waiting a jittered exponential
# time (the API's waits are longer than the CDN's).
GET_ATTEMPTS = 3
API_WAIT_INITIAL_S = 1
API_WAIT_MAX_S = 15
RAW_WAIT_INITIAL_S = 1
RAW_WAIT_MAX_S = 8

# A page's slug is its path's, at most this long; a path that leaves nothing is `readme`.
SLUG_MAX_CHARS = 120
SLUG_FALLBACK = "readme"

MD_EXTS = (".md", ".mdx", ".markdown")

# Top-level directories to skip entirely (paths starting with these).
SKIP_PREFIXES = (
    ".github/", ".gitlab/", ".vscode/", ".idea/", ".circleci/",
    "node_modules/", "vendor/", "tests/", "test/", "__tests__/",
    "spec/", "specs/", "fixtures/",
    "dist/", "build/", "out/", "target/", ".next/", ".nuxt/",
    "coverage/", "benchmarks/",
)

# Substring matches catch nested occurrences the prefix list misses.
SKIP_SUBSTRINGS = (
    "/node_modules/", "/vendor/", "/__tests__/", "/fixtures/",
)
