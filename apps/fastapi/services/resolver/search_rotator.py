"""
Search-API rotator — single-provider-at-a-time, quota-conserving.

CRITICAL ECONOMY RULE: every resolver call hits AT MOST ONE provider.
We never fan out parallel queries to Tavily + Exa + Linkup + Jina because
each has only 1k free queries/month — that would burn the whole pool in
a few days.

Provider rotation:
  - Round-robin AT THE START (each provider sees every Nth call)
  - Per-provider EWMA success rate (alpha=0.3) reorders priority over time
  - Quota-exhaustion (HTTP 429 OR our own monthly counter) → pin out for 24h
  - Per-provider monthly counter persisted in Redis (key per month)

Same pattern as the existing LiteLLM cascade for chat models — but
adapted for the "spend ONE provider's quota per call" constraint.

Providers in 2026 free tier (validated 2026-04-26 research):
  Exa     1000 req/mo free   — Exa Fast mode (sub-350ms, 1 credit/req)
  Tavily  1000 req/mo free   — search_depth=advanced, max_results=5
  Linkup  €5/mo ≈ 1000 std    — depth=standard
  Jina    generous free       — Reader+Search; returns clean Markdown
                                  (saves D0 hop when we want page content)
  Brave   PAID ONLY in 2026   — skip
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


# Free-tier monthly cap (conservative — leaves headroom).
_PER_PROVIDER_MONTHLY_CAP = int(os.environ.get("RESOLVER_SEARCH_MONTHLY_CAP", "950"))
# Cooldown when a provider 429s OR exhausts our monthly counter.
_QUOTA_COOLDOWN_SEC = 24 * 3600   # 24h — quota typically resets at month boundary anyway
_TRANSIENT_COOLDOWN_SEC = 300     # 5min for transient 5xx / network errors
_REQUEST_TIMEOUT_SEC = 12.0
_USER_AGENT = "COELHONexus-resolver/1.0"


@dataclass
class SearchResult:
    """Top result from whatever provider answered."""
    provider: str             # 'exa' | 'tavily' | 'linkup' | 'jina'
    url: str
    title: str = ""
    snippet: str = ""
    rank: int = 1             # always 1 for our use case (top result only)


@dataclass
class _ProviderState:
    """In-process state per provider; persisted parts go to Redis."""
    name: str
    api_key_env: str
    api_key: Optional[str] = None     # populated at startup
    quota_used_this_month: int = 0    # synced from Redis
    cooldown_until_ts: float = 0.0    # epoch seconds; 0 = available
    successes: int = 0                # EWMA-tracked
    failures: int = 0
    ewma_success_rate: float = 1.0    # starts optimistic; decays on miss

    @property
    def available(self) -> bool:
        if self.api_key is None:
            return False
        if time.time() < self.cooldown_until_ts:
            return False
        if self.quota_used_this_month >= _PER_PROVIDER_MONTHLY_CAP:
            return False
        return True

    def record_success(self):
        self.successes += 1
        self.ewma_success_rate = 0.7 * self.ewma_success_rate + 0.3 * 1.0

    def record_failure(self, transient: bool = False):
        self.failures += 1
        self.ewma_success_rate = 0.7 * self.ewma_success_rate + 0.3 * 0.0
        if transient:
            self.cooldown_until_ts = time.time() + _TRANSIENT_COOLDOWN_SEC

    def record_quota_exhausted(self):
        self.cooldown_until_ts = time.time() + _QUOTA_COOLDOWN_SEC


# Provider order = static priority. Within available providers, EWMA reorders.
# Exa Fast wins by latency; Tavily second for content quality; Linkup adds
# regional diversity; Jina last because of slower Reader-style response.
_DEFAULT_ORDER = ["exa", "tavily", "linkup", "jina"]


class SearchRotator:
    """
    Single-provider-at-a-time search.

    Pick provider via:
      1. Available (api_key set, not in cooldown, under monthly cap)
      2. Highest EWMA success rate
      3. Static priority tiebreaker

    Each search() call hits exactly ONE provider. On transient failure
    (5xx, network), demote and retry with the NEXT provider — but no parallel
    fan-out, no double-charging quota.
    """

    def __init__(self, redis_aio=None):
        self.redis = redis_aio   # optional; if None, quota counter is process-local only
        self.providers: dict[str, _ProviderState] = {
            "exa":    _ProviderState(name="exa",    api_key_env="EXA_API_KEY"),
            "tavily": _ProviderState(name="tavily", api_key_env="TAVILY_API_KEY"),
            "linkup": _ProviderState(name="linkup", api_key_env="LINKUP_API_KEY"),
            "jina":   _ProviderState(name="jina",   api_key_env="JINA_API_KEY"),
        }
        for p in self.providers.values():
            p.api_key = os.environ.get(p.api_key_env) or None

    # ----------------------------------------------------------------------
    # Quota persistence
    # ----------------------------------------------------------------------
    def _quota_key(self, provider: str) -> str:
        ym = datetime.now(timezone.utc).strftime("%Y-%m")
        return f"resolver:search:quota:{provider}:{ym}"

    async def _load_quota(self):
        """Sync per-provider counters from Redis (if connected)."""
        if self.redis is None:
            return
        for p in self.providers.values():
            try:
                v = await self.redis.get(self._quota_key(p.name))
                p.quota_used_this_month = int(v) if v else 0
            except Exception:
                pass

    async def _bump_quota(self, provider: str):
        self.providers[provider].quota_used_this_month += 1
        if self.redis is None:
            return
        try:
            key = self._quota_key(provider)
            await self.redis.incr(key)
            await self.redis.expire(key, 35 * 24 * 3600)   # ~5 weeks TTL
        except Exception:
            pass

    # ----------------------------------------------------------------------
    # Provider selection
    # ----------------------------------------------------------------------
    def _pick_provider(self, exclude: set[str] | None = None) -> Optional[str]:
        excl = exclude or set()
        candidates = [
            p for p in self.providers.values()
            if p.available and p.name not in excl
        ]
        if not candidates:
            return None
        # EWMA wins; static priority tiebreaker.
        priority = {name: i for i, name in enumerate(_DEFAULT_ORDER)}
        candidates.sort(
            key=lambda p: (-p.ewma_success_rate, priority.get(p.name, 99))
        )
        return candidates[0].name

    # ----------------------------------------------------------------------
    # Per-provider search implementations
    # ----------------------------------------------------------------------
    async def _search_exa(self, query: str, client: httpx.AsyncClient) -> Optional[SearchResult]:
        """Exa Fast mode — 1 credit, sub-350ms."""
        api_key = self.providers["exa"].api_key
        try:
            r = await client.post(
                "https://api.exa.ai/search",
                headers={"x-api-key": api_key, "content-type": "application/json"},
                json={
                    "query": query,
                    "type": "fast",
                    "numResults": 3,
                    "useAutoprompt": False,
                },
                timeout=_REQUEST_TIMEOUT_SEC,
            )
        except httpx.HTTPError as e:
            self.providers["exa"].record_failure(transient=True)
            logger.warning(f"[search.exa] error: {e}")
            return None
        if r.status_code == 429:
            self.providers["exa"].record_quota_exhausted()
            return None
        if r.status_code != 200:
            self.providers["exa"].record_failure(transient=r.status_code >= 500)
            return None
        results = (r.json() or {}).get("results") or []
        if not results:
            self.providers["exa"].record_success()
            await self._bump_quota("exa")
            return None
        top = results[0]
        await self._bump_quota("exa")
        self.providers["exa"].record_success()
        return SearchResult(
            provider="exa", url=top.get("url", ""),
            title=(top.get("title") or "")[:200],
            snippet=(top.get("text") or top.get("snippet") or "")[:300],
        )

    async def _search_tavily(self, query: str, client: httpx.AsyncClient) -> Optional[SearchResult]:
        """Tavily Search — search_depth=advanced for our use case."""
        api_key = self.providers["tavily"].api_key
        try:
            r = await client.post(
                "https://api.tavily.com/search",
                headers={"content-type": "application/json"},
                json={
                    "api_key": api_key,
                    "query": query,
                    "search_depth": "advanced",
                    "max_results": 5,
                    "include_answer": False,
                    "include_raw_content": False,
                },
                timeout=_REQUEST_TIMEOUT_SEC,
            )
        except httpx.HTTPError as e:
            self.providers["tavily"].record_failure(transient=True)
            logger.warning(f"[search.tavily] error: {e}")
            return None
        if r.status_code == 429:
            self.providers["tavily"].record_quota_exhausted()
            return None
        if r.status_code != 200:
            self.providers["tavily"].record_failure(transient=r.status_code >= 500)
            return None
        results = (r.json() or {}).get("results") or []
        if not results:
            self.providers["tavily"].record_success()
            await self._bump_quota("tavily")
            return None
        top = results[0]
        await self._bump_quota("tavily")
        self.providers["tavily"].record_success()
        return SearchResult(
            provider="tavily", url=top.get("url", ""),
            title=(top.get("title") or "")[:200],
            snippet=(top.get("content") or "")[:300],
        )

    async def _search_linkup(self, query: str, client: httpx.AsyncClient) -> Optional[SearchResult]:
        """Linkup — €5/mo free credit (~1000 standard searches)."""
        api_key = self.providers["linkup"].api_key
        try:
            r = await client.post(
                "https://api.linkup.so/v1/search",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "q": query,
                    "depth": "standard",
                    "outputType": "searchResults",
                },
                timeout=_REQUEST_TIMEOUT_SEC,
            )
        except httpx.HTTPError as e:
            self.providers["linkup"].record_failure(transient=True)
            logger.warning(f"[search.linkup] error: {e}")
            return None
        if r.status_code == 429:
            self.providers["linkup"].record_quota_exhausted()
            return None
        if r.status_code != 200:
            self.providers["linkup"].record_failure(transient=r.status_code >= 500)
            return None
        payload = r.json() or {}
        results = payload.get("results") or []
        if not results:
            self.providers["linkup"].record_success()
            await self._bump_quota("linkup")
            return None
        top = results[0]
        await self._bump_quota("linkup")
        self.providers["linkup"].record_success()
        return SearchResult(
            provider="linkup", url=top.get("url", ""),
            title=(top.get("name") or top.get("title") or "")[:200],
            snippet=(top.get("content") or top.get("snippet") or "")[:300],
        )

    async def _search_jina(self, query: str, client: httpx.AsyncClient) -> Optional[SearchResult]:
        """Jina Search — `s.jina.ai/{query}` returns clean MD of top results."""
        api_key = self.providers["jina"].api_key
        try:
            url = f"https://s.jina.ai/{query}"
            headers = {"Accept": "application/json", "X-Respond-With": "no-content"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            r = await client.get(url, headers=headers, timeout=_REQUEST_TIMEOUT_SEC)
        except httpx.HTTPError as e:
            self.providers["jina"].record_failure(transient=True)
            logger.warning(f"[search.jina] error: {e}")
            return None
        if r.status_code == 429:
            self.providers["jina"].record_quota_exhausted()
            return None
        if r.status_code != 200:
            self.providers["jina"].record_failure(transient=r.status_code >= 500)
            return None
        try:
            payload = r.json() or {}
        except ValueError:
            return None
        data = payload.get("data") or []
        if not data:
            self.providers["jina"].record_success()
            await self._bump_quota("jina")
            return None
        top = data[0]
        await self._bump_quota("jina")
        self.providers["jina"].record_success()
        return SearchResult(
            provider="jina", url=top.get("url", ""),
            title=(top.get("title") or "")[:200],
            snippet=(top.get("description") or "")[:300],
        )

    # ----------------------------------------------------------------------
    # Public interface — ONE call per resolver invocation
    # ----------------------------------------------------------------------
    async def search(
        self, query: str, *, client: Optional[httpx.AsyncClient] = None,
    ) -> Optional[SearchResult]:
        """
        Run ONE search query against ONE selected provider. Returns the top
        result OR None when:
          - no provider available (all keys missing OR quota-exhausted OR cooldown)
          - the chosen provider returned 0 results

        On transient failure (5xx, network), demotes the provider and tries
        the NEXT one — but only THE next, no fan-out. Max 2 attempts per call
        to bound quota burn.
        """
        await self._load_quota()

        own_client = client is None
        if own_client:
            client = httpx.AsyncClient(headers={"User-Agent": _USER_AGENT})

        try:
            tried: set[str] = set()
            for _ in range(2):  # at most 1 retry on transient failure
                provider_name = self._pick_provider(exclude=tried)
                if provider_name is None:
                    return None
                tried.add(provider_name)

                impl = {
                    "exa":    self._search_exa,
                    "tavily": self._search_tavily,
                    "linkup": self._search_linkup,
                    "jina":   self._search_jina,
                }[provider_name]

                logger.info(
                    f"[search.rotator] using provider={provider_name} "
                    f"quota={self.providers[provider_name].quota_used_this_month}/"
                    f"{_PER_PROVIDER_MONTHLY_CAP}"
                )
                result = await impl(query, client)
                if result is not None:
                    return result
                # If we got None due to transient failure, the provider state
                # already records cooldown via record_failure(transient=True).
                # Loop tries the next available provider.
            return None
        finally:
            if own_client and client is not None:
                await client.aclose()

    def status(self) -> dict:
        """Snapshot of provider state — useful for /debug endpoints."""
        return {
            name: {
                "configured": p.api_key is not None,
                "quota_used": p.quota_used_this_month,
                "quota_cap": _PER_PROVIDER_MONTHLY_CAP,
                "available": p.available,
                "ewma_success_rate": round(p.ewma_success_rate, 3),
                "successes": p.successes,
                "failures": p.failures,
                "cooldown_until": p.cooldown_until_ts,
            }
            for name, p in self.providers.items()
        }
