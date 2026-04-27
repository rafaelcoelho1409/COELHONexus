"""
D0 root liveness probe — content-validate that a docs URL is genuinely a
live docs site, not a parked domain / SPA shell / 200-but-not-found.

Pure HTTP + regex — no LLM, no crawler, no search API.

Classification:
  LIVE        ≥2 docs signals (nav, headings, code, sidebar, search, docs/markdown words)
              AND ≥400 chars text after tag-strip
              AND not parked
              AND not off-host redirected
  EMPTY_SHELL reachable but body too small / few signals (SPA skeleton)
  PARKED      domain-for-sale markers
  DEAD        HTTP ≥400 OR off-host redirect
  ERROR       network failure
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Literal, Optional
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)


_TIMEOUT_SEC = 5.0
_MAX_BODY_BYTES = 50_000

_DOCS_SIGNALS: list[tuple[str, str]] = [
    ("nav",             r"<nav\b"),
    ("sidebar",         r"(?:class|id)\s*=\s*\"[^\"]*(?:sidebar|toc|navigation)"),
    ("headings",        r"<h[1-3]\b"),
    ("code",            r"<(?:code|pre)\b"),
    ("docs_word",       r"(?i)\b(docs?|documentation|api reference|guide|tutorial)\b"),
    ("markdown_word",   r"(?i)\bmarkdown\b|\.md\b"),
    ("search_ui",       r"(?:class|id)\s*=\s*\"[^\"]*(?:search|algolia)"),
]

_PARKED_MARKERS = [
    "this domain is for sale",
    "domain is for sale",
    "buy this domain",
    "purchase this domain",
    "sedo.com/search",
    "hugedomains.com",
    "domainmarket.com",
    "parking service",
    "godaddy.com/domainsearch",
    "namecheap.com/market",
]

_MIN_LIVE_TEXT_CHARS = 400
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _strip_tags(html: str) -> str:
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", html)).strip()


def _detect_signals(body: str) -> list[str]:
    head = body[:50_000]
    return [name for name, pat in _DOCS_SIGNALS if re.search(pat, head)]


def _is_parked(body: str) -> bool:
    lower = body[:20_000].lower()
    return any(marker in lower for marker in _PARKED_MARKERS)


def _is_off_host(original: str, final: str) -> bool:
    o = (urlparse(original).netloc or "").lower()
    f = (urlparse(final).netloc or "").lower()
    if not o or not f or o == f:
        return False
    return not (o.endswith("." + f) or f.endswith("." + o))


RootLivenessStatus = Literal["LIVE", "EMPTY_SHELL", "PARKED", "DEAD", "ERROR"]


@dataclass
class RootLiveness:
    url: str
    status: RootLivenessStatus
    http_status: int
    reason: str
    bytes_read: int = 0
    docs_signals: list[str] = field(default_factory=list)
    final_url: Optional[str] = None

    @property
    def usable(self) -> bool:
        return self.status == "LIVE"


async def probe_root_liveness(
    docs_url: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> RootLiveness:
    if not docs_url:
        return RootLiveness(
            url="", status="ERROR", http_status=-99, reason="empty URL",
        )

    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(
            headers={"User-Agent": "COELHONexus-resolver/1.0"},
            timeout=_TIMEOUT_SEC,
        )

    try:
        try:
            r = await client.get(
                docs_url, timeout=_TIMEOUT_SEC, follow_redirects=True,
            )
        except httpx.TimeoutException:
            return RootLiveness(
                url=docs_url, status="ERROR", http_status=-1, reason="timeout",
            )
        except httpx.HTTPError as e:
            return RootLiveness(
                url=docs_url, status="ERROR", http_status=-2,
                reason=f"{type(e).__name__}: {str(e)[:120]}",
            )

        body = r.text[:_MAX_BODY_BYTES] if r.text else ""
        final_url = str(r.url)
        bytes_read = len(body)

        if r.status_code >= 400:
            return RootLiveness(
                url=docs_url, status="DEAD", http_status=r.status_code,
                reason=f"HTTP {r.status_code}",
                bytes_read=bytes_read, final_url=final_url,
            )

        if _is_off_host(docs_url, final_url):
            return RootLiveness(
                url=docs_url, status="DEAD", http_status=r.status_code,
                reason=f"off-host redirect → {urlparse(final_url).netloc}",
                bytes_read=bytes_read, final_url=final_url,
            )

        if _is_parked(body):
            return RootLiveness(
                url=docs_url, status="PARKED", http_status=r.status_code,
                reason="domain-for-sale markers detected",
                bytes_read=bytes_read, final_url=final_url,
            )

        text = _strip_tags(body)
        signals = _detect_signals(body)

        if len(text) < _MIN_LIVE_TEXT_CHARS:
            return RootLiveness(
                url=docs_url, status="EMPTY_SHELL", http_status=r.status_code,
                reason=f"only {len(text)} chars text (SPA shell?)",
                bytes_read=bytes_read, docs_signals=signals, final_url=final_url,
            )
        if len(signals) < 2:
            return RootLiveness(
                url=docs_url, status="EMPTY_SHELL", http_status=r.status_code,
                reason=f"only {len(signals)} docs signals — looks non-docs",
                bytes_read=bytes_read, docs_signals=signals, final_url=final_url,
            )

        return RootLiveness(
            url=docs_url, status="LIVE", http_status=r.status_code,
            reason=f"{len(text)} chars, {len(signals)} signals",
            bytes_read=bytes_read, docs_signals=signals, final_url=final_url,
        )
    finally:
        if own_client and client is not None:
            await client.aclose()
