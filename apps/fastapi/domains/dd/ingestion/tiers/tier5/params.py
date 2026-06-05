"""Tier 5 — tunable scalars + endpoint constants + path filter lists."""
from __future__ import annotations


USER_AGENT = "COELHONexus-DocsDistiller-Tier5/1.0"
TIMEOUT_S = 30.0
CONCURRENCY = 10
MIN_OK_BYTES = 150

API_BASE = "https://api.github.com"
RAW_BASE = "https://raw.githubusercontent.com"

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
