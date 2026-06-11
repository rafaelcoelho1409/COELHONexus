"""HN Algolia API client configuration — frozen-dataclass GROUP per
docs/CODE-CONVENTIONS.md §3.

Six related tunables describing one concept ("how this tool talks to HN").
No API key needed — Algolia HN search is fully open with 10k req/hr/IP.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class HNConfig:
    """HN Algolia API client knobs. Docs: https://hn.algolia.com/api"""

    base_url: str = "https://hn.algolia.com/api/v1"
    user_agent: str = "COELHO-Nexus-Research-Radar/1.0 (mailto:rafaelcoelho1409@gmail.com)"
    timeout_s: float = 30.0
    # Algolia HN gives 10k req/hr/IP ≈ 2.8 req/s. We're polite at 0.5 s
    # (= ~7,200/hr ceiling) — leaves plenty of room for concurrent radar
    # scans without ever brushing the limit.
    min_request_interval_s: float = 0.5
    # Algolia caps hitsPerPage at 1000 but the radar doesn't need that many
    # in a single shot — sanity-cap at 100 to keep responses bounded.
    max_results_per_call: int = 100


HN = HNConfig()
