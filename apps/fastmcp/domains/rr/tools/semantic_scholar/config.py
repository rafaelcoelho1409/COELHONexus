"""Semantic Scholar API client configuration — frozen-dataclass GROUP per
docs/CODE-CONVENTIONS.md §3. Same shape as arxiv/config.py.

Seven related tunables describing one concept ("how this tool talks to S2"):
endpoint, identity, timeout, two rate-limit modes (unauth vs free-keyed), the
API-cap on per-call result count, and the env var name carrying the optional
API key. Tool.register() reads `min_request_interval_*_s` to decide which
interval to declare to the cross-cutting RateLimitMiddleware at startup.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SemanticScholarConfig:
    """S2 API client knobs. Docs: https://api.semanticscholar.org/api-docs/graph"""

    base_url: str = "https://api.semanticscholar.org/graph/v1"
    user_agent: str = "COELHO-Nexus-Research-Radar/1.0 (mailto:rafaelcoelho1409@gmail.com)"
    timeout_s: float = 30.0

    # Unauth: 100 req/5min ≈ 3s/request (S2's documented shared pool).
    min_request_interval_s: float = 3.0
    # With a FREE API key (sign-up only, no payment — passes the $0 + no-paid-
    # SaaS rule): up to 1 RPS sustained.
    min_request_interval_keyed_s: float = 1.0

    # S2 API hard cap on the `limit` parameter per /paper/search call.
    max_results_per_call: int = 100

    # Optional. When the env var is set, tool.register() picks the keyed
    # interval and service._build_headers() attaches `x-api-key`. Both paths
    # work without it (just slower).
    api_key_env: str = "SEMANTIC_SCHOLAR_API_KEY"


S2 = SemanticScholarConfig()
