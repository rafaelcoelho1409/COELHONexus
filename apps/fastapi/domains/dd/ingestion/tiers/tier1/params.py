"""Tier 1 — tunable scalars."""
from __future__ import annotations


USER_AGENT = "COELHONexus-DocsDistiller-Tier1/1.0"
TIMEOUT_S = 30.0
MIN_OK_BYTES = 500

# Heuristics for detecting a llms.txt-style link index disguised as
# llms-full.txt (the bundle should have many fenced code blocks; a manifest
# has many URL: pointers and few/no fences).
MANIFEST_MIN_URL_LINES = 100
MANIFEST_MAX_FENCES = 5
