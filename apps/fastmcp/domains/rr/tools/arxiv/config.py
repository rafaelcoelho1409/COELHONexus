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

    # arXiv answers overload with a bare 406 (not 429) — confirmed via
    # multiple 2026 community reports (e.g. sdewell/code-quorum#2,
    # pkuppens/production-agentic-rag-course#40) — and it does NOT clear
    # on an immediate retry, so back off first. 2 attempts, backoff
    # multiplies by attempt number (5s, then 10s).
    retry_max_attempts: int = 2
    retry_backoff_base_s: float = 5.0

    # Circuit breaker cooldown once retries are exhausted — short enough
    # that a genuinely-recovered arXiv is usable again within the same
    # scan, long enough that the REST of this scan's discovery subagents
    # (and a re-triggered scan) don't each independently re-discover the
    # same overload with a live request.
    circuit_breaker_cooldown_s: float = 120.0


ARXIV = ArxivConfig()
