"""
Direct llms.txt HEAD probe (Layer 4.5).

Catches projects that publish llms.txt OR llms-full.txt at conventional
paths but aren't registered in any directory mirror.

Per the validated 2026-04-26 research: ~50% of top frameworks DON'T
publish llms-full.txt at the conventional `{base}/llms-full.txt` path
(FastAPI = 404, Cursor = broken, LangChain/Pydantic = redirect-to-wrong).
So we probe THREE common URL patterns per type with strict content
validation, accept whatever passes.

Patterns tried (per llms_type):
  - {base}/{filename}
  - {base}/docs/{filename}
  - {base}/latest/{filename}

Validation gates (all required):
  - HTTP 200 after redirect chain
  - Body length ≥ 200 bytes (rejects "Redirecting..." stubs)
  - Body does NOT start with `<` (rejects HTML pages)
  - No "redirecting"/"not found"/"404" sentinels in first 200 chars
  - For llms.txt: at least one [text](url) link pattern
  - For llms-full.txt: at least one `#` heading line + ≥5 KB body
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Literal, Optional
from urllib.parse import urljoin

import httpx

logger = logging.getLogger(__name__)


_TIMEOUT_SEC = 6.0
_USER_AGENT = "COELHONexus-resolver/1.0"
_MAX_BODY_BYTES = 8192  # only inspect first 8KB for validation


@dataclass
class LlmsTxtProbeResult:
    """Result of a direct llms.txt / llms-full.txt probe."""
    found: bool
    url: Optional[str] = None
    type: Literal["llms_txt", "llms_full_txt"] = "llms_txt"
    bytes_inspected: int = 0
    reason: str = ""


def _candidate_urls(base_url: str, filename: str) -> list[str]:
    """Generate the 3 common URL patterns to probe."""
    if not base_url:
        return []
    base = base_url if base_url.endswith("/") else base_url + "/"
    return [
        urljoin(base, filename),
        urljoin(base, f"docs/{filename}"),
        urljoin(base, f"latest/{filename}"),
    ]


def _validate_body(
    body: str, llms_type: Literal["llms_txt", "llms_full_txt"],
) -> tuple[bool, str]:
    """Apply content validation gates. Returns (ok, reason)."""
    if not body:
        return False, "empty body"

    size = len(body)
    min_size = 200 if llms_type == "llms_txt" else 5000
    if size < min_size:
        return False, f"body too small ({size} < {min_size})"

    stripped = body.lstrip()
    if stripped.startswith("<"):
        return False, "body starts with '<' — HTML page (not the file)"

    lower = stripped[:200].lower()
    for sentinel in (
        "redirecting", "not found", "page not found", "404",
        "this page does not exist", "moved permanently",
    ):
        if sentinel in lower:
            return False, f"placeholder sentinel '{sentinel}' present"

    if llms_type == "llms_txt":
        # llms.txt MUST have [text](url) link patterns.
        if not re.search(r"\[[^\]]+\]\([^)]+\)", body):
            return False, "no `[text](url)` link patterns — not llms.txt format"
    else:
        # llms-full.txt MUST have at least one `#` heading.
        if not any(line.lstrip().startswith("#") for line in body.split("\n")):
            return False, "no '#' heading line — not Markdown-shaped"

    return True, "ok"


async def _probe_one(
    client: httpx.AsyncClient,
    url: str,
    llms_type: Literal["llms_txt", "llms_full_txt"],
) -> tuple[bool, int, str]:
    """Probe one URL. Returns (passed, bytes_inspected, reason)."""
    try:
        # Use Range header to limit body size; servers ignoring Range still
        # return full body (we slice client-side).
        r = await client.get(
            url,
            timeout=_TIMEOUT_SEC,
            follow_redirects=True,
            headers={"Range": f"bytes=0-{_MAX_BODY_BYTES - 1}"},
        )
    except httpx.HTTPError as e:
        return False, 0, f"{type(e).__name__}: {str(e)[:80]}"

    if r.status_code not in (200, 206):
        return False, 0, f"HTTP {r.status_code}"

    body = (r.text or "")[:_MAX_BODY_BYTES]
    ok, reason = _validate_body(body, llms_type)
    return ok, len(body), reason


async def probe_llmstxt(
    docs_url: str,
    llms_type: Literal["llms_txt", "llms_full_txt"] = "llms_txt",
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> LlmsTxtProbeResult:
    """
    Probe the 3 common paths for the given llms_type. Returns the FIRST
    URL that passes all validation gates, OR a failure result with
    the last attempted reason.
    """
    if not docs_url:
        return LlmsTxtProbeResult(found=False, type=llms_type, reason="empty docs_url")

    filename = "llms-full.txt" if llms_type == "llms_full_txt" else "llms.txt"
    urls = _candidate_urls(docs_url, filename)

    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(
            headers={"User-Agent": _USER_AGENT}, timeout=_TIMEOUT_SEC,
        )

    try:
        # Probe sequentially (NOT parallel) — the 1st pattern works ~70% of the
        # time per the URL conventions; sequential saves bandwidth on common case.
        last_reason = ""
        for url in urls:
            passed, bytes_read, reason = await _probe_one(client, url, llms_type)
            if passed:
                return LlmsTxtProbeResult(
                    found=True, url=url, type=llms_type,
                    bytes_inspected=bytes_read, reason="validated",
                )
            last_reason = f"{url} → {reason}"
        return LlmsTxtProbeResult(
            found=False, type=llms_type,
            reason=f"3/3 patterns failed; last: {last_reason}",
        )
    finally:
        if own_client and client is not None:
            await client.aclose()
