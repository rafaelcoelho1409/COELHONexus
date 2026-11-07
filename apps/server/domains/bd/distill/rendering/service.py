"""Render validated, internally generated Learning Guide HTML to PDF."""
from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import infra

from . import params


async def render_pdf(report_html: str) -> bytes:
    """Use the isolated headless browser service; block all page network access."""
    body = report_html.encode("utf-8")
    if not body or len(body) > params.MAX_HTML_BYTES:
        raise ValueError("Learning Guide HTML is empty or exceeds the PDF render limit")

    cdp_endpoint = infra.playwright.params.CDP_HEADLESS
    if not cdp_endpoint:
        raise RuntimeError("the headless PDF renderer is not configured")
    ws_url = await asyncio.to_thread(infra.playwright.service.resolve_ws_url, cdp_endpoint, cache = True)
    if not ws_url:
        raise RuntimeError("the headless PDF renderer is unavailable")

    playwright = await infra.playwright.service.start_playwright()
    context = None
    try:
        browser = await playwright.chromium.connect_over_cdp(ws_url, timeout = params.CONNECT_TIMEOUT_MS)
        context = await browser.new_context(java_script_enabled = False, accept_downloads = False)
        page = await context.new_page()

        async def block_external_request(route: Any) -> None:
            await route.abort()

        await page.route("**/*", block_external_request)
        await page.set_content(report_html, wait_until = "load", timeout = params.RENDER_TIMEOUT_MS)
        pdf = await page.pdf(
            format = "A4",
            print_background = True,
            prefer_css_page_size = True,
        )
        if not pdf.startswith(b"%PDF-") or len(pdf) > params.MAX_PDF_BYTES:
            raise RuntimeError("the PDF renderer returned an invalid or oversized artifact")
        return pdf
    except asyncio.CancelledError:
        raise
    except Exception as error:
        raise RuntimeError("the headless browser failed to render the Learning Guide PDF") from error
    finally:
        if context is not None:
            with contextlib.suppress(Exception):
                await context.close()
        with contextlib.suppress(Exception):
            await playwright.stop()
