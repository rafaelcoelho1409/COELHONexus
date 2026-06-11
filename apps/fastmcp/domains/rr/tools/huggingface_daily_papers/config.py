"""HuggingFace Daily Papers API client configuration — frozen-dataclass GROUP
per docs/CODE-CONVENTIONS.md §3.

Six related tunables describing one concept ("how this tool talks to HF").
No API key needed — the daily_papers endpoint is fully open. No keys.py
sibling because there are no identifier registries (no field-list config,
no category enums) — the API returns everything for the day.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class HuggingFaceDailyPapersConfig:
    """HF Daily Papers API client knobs.

    Endpoint is undocumented but stable since 2024 — used by the
    `huggingface.co/papers` UI. Surfaces curation + community upvotes
    for ML papers, all linked to arxiv IDs.
    """

    base_url: str = "https://huggingface.co"
    daily_papers_path: str = "/api/daily_papers"
    user_agent: str = "COELHO-Nexus-Research-Radar/1.0 (mailto:rafaelcoelho1409@gmail.com)"
    timeout_s: float = 30.0
    # HF doesn't document a strict rate limit on this endpoint. 1 req/s is the
    # polite default that matches our other unauth tool baselines.
    min_request_interval_s: float = 1.0
    # HF curates ~10-30 papers/day; we cap higher to leave the client-side
    # `n_max` trim room without ever clipping the API's natural batch.
    max_results_per_call: int = 50


HF_DAILY = HuggingFaceDailyPapersConfig()
