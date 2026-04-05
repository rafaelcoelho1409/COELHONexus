#!/usr/bin/env python3
"""
Debug script - check panel content after longer wait
"""
import asyncio
import json
from playwright.async_api import async_playwright

CDP_HEADED = "https://playwright-cdp.YOUR_TAILNET_DOMAIN.ts.net"
VIDEO_ID = "wGJMlkBLBRI"

def get_cdp_url(endpoint: str) -> str:
    import ssl
    import urllib.request
    from urllib.parse import urlparse
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    parsed = urlparse(endpoint)
    with urllib.request.urlopen(f"{endpoint}/json/version", context=ctx) as resp:
        data = json.loads(resp.read())
        ws_url = data["webSocketDebuggerUrl"]
        ws_parsed = urlparse(ws_url)
        if parsed.scheme == "https":
            return f"wss://{parsed.netloc}{ws_parsed.path}"
        return ws_url

async def main():
    url = f"https://www.youtube.com/watch?v={VIDEO_ID}"
    cdp_url = get_cdp_url(CDP_HEADED)
    print(f"Testing video: {VIDEO_ID}")

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(cdp_url)
        context = await browser.new_context(viewport={"width": 1280, "height": 720})
        page = await context.new_page()

        await page.goto(url, wait_until="load")
        await page.wait_for_timeout(3000)

        # Click expand
        await page.evaluate('''() => {
            const btn = document.querySelector('tp-yt-paper-button#expand:not([hidden])');
            if (btn) { btn.click(); }
        }''')
        await page.wait_for_timeout(1500)

        # Click Show transcript
        await page.evaluate('''() => {
            const btn = document.querySelector('[aria-label="Show transcript"]');
            if (btn) { btn.scrollIntoView({ block: 'center' }); btn.click(); }
        }''')

        print("Clicked Show transcript, waiting 5 seconds...")
        await page.wait_for_timeout(5000)

        # Check panel content in detail
        panel_info = await page.evaluate('''() => {
            const panel = document.querySelector(
                'ytd-engagement-panel-section-list-renderer[visibility="ENGAGEMENT_PANEL_VISIBILITY_EXPANDED"]'
            );
            if (!panel) return { error: 'No expanded panel' };

            return {
                targetId: panel.getAttribute('target-id'),
                childTags: Array.from(panel.children).map(c => c.tagName).slice(0, 10),
                // Check for various transcript element types
                segmentViewModels: panel.querySelectorAll('transcript-segment-view-model').length,
                segmentRenderers: panel.querySelectorAll('ytd-transcript-segment-renderer').length,
                segmentTexts: panel.querySelectorAll('.segment-text').length,
                transcriptRenderer: !!panel.querySelector('ytd-transcript-renderer'),
                transcriptSearchPanel: !!panel.querySelector('ytd-transcript-search-panel-renderer'),
                segmentList: !!panel.querySelector('ytd-transcript-segment-list-renderer'),
                // Check innerText
                hasTimestamps: /\\d+:\\d{2}/.test(panel.innerText),
                innerTextPreview: panel.innerText?.slice(0, 500),
                // Check for loading indicators
                hasSpinner: !!panel.querySelector('yt-spinner, paper-spinner, .spinner'),
                hasLoading: panel.innerHTML.includes('loading'),
            };
        }''')
        print(f"\nPanel info after 5s wait:")
        print(json.dumps(panel_info, indent=2, ensure_ascii=False))

        # Try scrolling the panel
        print("\nScrolling panel to trigger lazy load...")
        await page.evaluate('''() => {
            const panel = document.querySelector(
                'ytd-engagement-panel-section-list-renderer[visibility="ENGAGEMENT_PANEL_VISIBILITY_EXPANDED"]'
            );
            if (panel) {
                const content = panel.querySelector('#content') || panel;
                content.scrollTop = 200;
                content.scrollTop = 0;
            }
        }''')
        await page.wait_for_timeout(3000)

        # Check again
        panel_info2 = await page.evaluate('''() => {
            const panel = document.querySelector(
                'ytd-engagement-panel-section-list-renderer[visibility="ENGAGEMENT_PANEL_VISIBILITY_EXPANDED"]'
            );
            if (!panel) return { error: 'No expanded panel' };
            return {
                segmentViewModels: panel.querySelectorAll('transcript-segment-view-model').length,
                segmentRenderers: panel.querySelectorAll('ytd-transcript-segment-renderer').length,
                hasTimestamps: /\\d+:\\d{2}/.test(panel.innerText),
                innerTextLength: panel.innerText?.length,
            };
        }''')
        print(f"\nPanel info after scroll:")
        print(json.dumps(panel_info2, indent=2))

        await page.screenshot(path=f"/tmp/debug2_{VIDEO_ID}.png")
        print(f"\nScreenshot saved to /tmp/debug2_{VIDEO_ID}.png")

        await context.close()

if __name__ == "__main__":
    asyncio.run(main())
