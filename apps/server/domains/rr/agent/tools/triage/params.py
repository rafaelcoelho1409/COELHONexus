"""Tunables for the RR agent's triage tool."""
from __future__ import annotations


# Source-diversity quota — only kicks in when ≥2 sources EACH contributed
# at least this many candidates. Below the floor we don't force diversity
# (a 5-arxiv + 0-hn pool shouldn't be artificially split).
MIN_PER_SOURCE_FLOOR: int = 3
