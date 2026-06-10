"""arXiv API client configuration — frozen-dataclass GROUP per
docs/CODE-CONVENTIONS.md §3.

Six related tunables that describe one concept ("how this tool talks to
arXiv") and would re-tune together → exactly the case the conventions
reserve `config.py` for. Module exports `ARXIV = ArxivConfig()` so call
sites read `ARXIV.timeout_s` (grouped, immutable) rather than scattered
loose constants.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ArxivConfig:
    """arXiv API client knobs. See https://info.arxiv.org/help/api/index.html."""

    base_url: str = "https://export.arxiv.org/api/query"
    # arXiv's polite-rate guidance asks for a User-Agent identifying the
    # caller (so they can contact us before rate-limiting). mailto: in the
    # UA also gets us into the polite pool at Crossref/Unpaywall later.
    user_agent: str = "COELHO-Nexus-Research-Radar/1.0 (mailto:rafaelcoelho1409@gmail.com)"
    timeout_s: float = 30.0
    # arXiv ToS: one request per three seconds, per IP. Enforced
    # process-locally by service._AsyncRateLimiter; a Redis-backed limiter
    # goes into apps/fastmcp/shared/middleware/ratelimit.py when we scale
    # beyond one pod.
    min_request_interval_s: float = 3.0
    # arXiv caps a single query at 2000 results; we cap much lower so a
    # runaway agent can't blow the budget.
    max_results_per_call: int = 100


ARXIV = ArxivConfig()
