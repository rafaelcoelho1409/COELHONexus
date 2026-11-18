"""Shared fetching for the tiers that crawl a site — retries, a site's rate limits honoured, one pace per crawl."""
from __future__ import annotations
from . import domain, params, patterns, service


__all__ = ["domain", "params", "patterns", "service"]
