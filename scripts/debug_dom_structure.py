#!/usr/bin/env python3
"""
Debug script to analyze transcript segment DOM structure
"""
import asyncio
import json
from playwright.async_api import async_playwright

CDP_HEADED = "https://playwright-cdp.YOUR_TAILNET_DOMAIN.ts.net"
VIDEO_ID = "lX4S3_wAi-4"

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
    print(f"Connecting to CDP: {cdp_url[:60]}...")

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(cdp_url)
        context = await browser.new_context(viewport={"width": 1280, "height": 720})
        page = await context.new_page()

        print(f"Navigating to: {url}")
        await page.goto(url, wait_until="load")
        await page.wait_for_timeout(3000)

        # Expand and click transcript
        try:
            expand_btn = page.locator('tp-yt-paper-button#expand:not([hidden])').first
            if await expand_btn.count() > 0:
                await expand_btn.click(timeout=3000)
                await page.wait_for_timeout(1000)
        except:
            pass

        await page.evaluate('''
            () => {
                const btn = document.querySelector('[aria-label="Show transcript"]');
                if (btn) { btn.click(); }
            }
        ''')
        await page.wait_for_timeout(3000)

        # Analyze segment structure
        print("\n=== Analyzing transcript-segment-view-model structure ===")

        result = await page.evaluate('''
            () => {
                const segments = document.querySelectorAll('transcript-segment-view-model');
                const analysis = [];
                const first5 = Array.from(segments).slice(0, 5);

                for (const seg of first5) {
                    // Get all children and their roles
                    const children = [];
                    for (const child of seg.querySelectorAll('*')) {
                        if (child.innerText?.trim()) {
                            children.push({
                                tag: child.tagName,
                                className: child.className?.slice(0, 50) || '',
                                text: child.innerText?.trim().slice(0, 50) || '',
                                isTimestamp: /^\\d+:\\d{2}$/.test(child.innerText?.trim() || '')
                            });
                        }
                    }
                    analysis.push({
                        outerHTML: seg.outerHTML.slice(0, 300),
                        innerText: seg.innerText?.slice(0, 100),
                        children: children.slice(0, 10)
                    });
                }

                return {
                    totalSegments: segments.length,
                    first5: analysis
                };
            }
        ''')

        print(f"Total segments: {result['totalSegments']}")
        for i, seg in enumerate(result.get('first5', [])):
            print(f"\n--- Segment {i} ---")
            print(f"innerText: {seg.get('innerText', '')[:80]}")
            print("Children with text:")
            for child in seg.get('children', []):
                ts_marker = " [TIMESTAMP]" if child.get('isTimestamp') else ""
                print(f"  {child['tag']}.{child['className'][:20]}: '{child['text'][:30]}'{ts_marker}")

        await context.close()
        print("\n=== Debug complete ===")

if __name__ == "__main__":
    asyncio.run(main())
