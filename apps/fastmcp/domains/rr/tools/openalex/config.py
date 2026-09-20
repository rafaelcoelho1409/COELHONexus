"""OpenAlex API client configuration — frozen-dataclass GROUP per
docs/CODE-CONVENTIONS.md §3.

Related tunables that describe one concept ("how this tool talks to
OpenAlex") and would re-tune together. Module exports `OPENALEX =
OpenAlexConfig()` so call sites read `OPENALEX.timeout_s` (grouped,
immutable) rather than scattered loose constants.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OpenAlexConfig:
    """OpenAlex API client knobs. See https://docs.openalex.org/."""

    base_url: str = "https://api.openalex.org/works"
    # OpenAlex's "polite pool" — tagging every request with an email (via
    # the `mailto` query param, not a header) gets faster + more
    # consistent response times and is the documented alternative to an
    # API key (OpenAlex has none). Same identity as the other 3 tools'
    # User-Agent mailto.
    mailto: str = "rafaelcoelho1409@gmail.com"
    user_agent: str = "COELHO-Nexus-Research-Radar/1.0 (mailto:rafaelcoelho1409@gmail.com)"
    timeout_s: float = 30.0
    # No hard rate limit is documented for polite-pool callers (unlike
    # arxiv's 1-req/3s ToS or S2's contested shared pool) — this interval
    # is just good-citizen pacing, not a documented ceiling we're avoiding.
    min_request_interval_s: float = 1.0
    # OpenAlex's `per_page` cap is 200; we cap lower so a runaway agent
    # can't blow the budget, matching the other 3 tools' pattern.
    max_results_per_call: int = 100


OPENALEX = OpenAlexConfig()
