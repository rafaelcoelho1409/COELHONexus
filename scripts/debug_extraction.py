#!/usr/bin/env python3
"""
Debug script to test the exact extraction logic from helpers.py
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

        # Expand description
        try:
            expand_btn = page.locator('tp-yt-paper-button#expand:not([hidden])').first
            if await expand_btn.count() > 0:
                await expand_btn.click(timeout=3000)
                print("Description expanded")
                await page.wait_for_timeout(1000)
        except Exception as e:
            print(f"Expand failed: {e}")

        # Click "Show transcript"
        clicked = await page.evaluate('''
            () => {
                const btn = document.querySelector('[aria-label="Show transcript"]');
                if (btn) {
                    btn.scrollIntoView({ block: 'center' });
                    btn.click();
                    return 'clicked';
                }
                return 'not found';
            }
        ''')
        print(f"Transcript button: {clicked}")
        await page.wait_for_timeout(3000)

        # Test the exact extraction logic from helpers.py
        print("\n=== Testing _extract_transcript_text logic ===")

        result = await page.evaluate('''
            () => {
                const debug = {};

                // Method 1: Feb 2026 UI - transcript-segment-view-model with .segment-text
                const segmentTexts = document.querySelectorAll(
                    'ytd-engagement-panel-section-list-renderer[target-id="engagement-panel-searchable-transcript"] .segment-text'
                );
                debug.method1_segments = segmentTexts.length;
                if (segmentTexts.length > 0) {
                    const parts = [];
                    segmentTexts.forEach(el => {
                        const container = el.closest('ytd-transcript-segment-renderer, transcript-segment-view-model');
                        const timestamp = container?.querySelector('.segment-timestamp')?.innerText?.trim() || '';
                        const text = el.innerText?.trim() || '';
                        if (timestamp && text) {
                            parts.push(timestamp + '\\n' + text);
                        } else if (text) {
                            parts.push(text);
                        }
                    });
                    if (parts.length > 0) {
                        debug.method1_result = 'SUCCESS: ' + parts.join('\\n').slice(0, 200);
                        return debug;
                    }
                }
                debug.method1_result = 'FAIL: no segment-text found';

                // Method 2: transcript-segment-view-model with yt-core-attributed-string
                const coreStrings = document.querySelectorAll(
                    'transcript-segment-view-model .yt-core-attributed-string'
                );
                debug.method2_coreStrings = coreStrings.length;
                if (coreStrings.length > 0) {
                    const parts = [];
                    coreStrings.forEach(el => {
                        const text = el.innerText?.trim();
                        if (text) parts.push(text);
                    });
                    if (parts.length > 0) {
                        debug.method2_result = 'SUCCESS: ' + parts.join('\\n').slice(0, 200);
                        return debug;
                    }
                }
                debug.method2_result = 'FAIL: no yt-core-attributed-string found';

                // Method 3: New panel by target-id
                const newPanel = document.querySelector(
                    'ytd-engagement-panel-section-list-renderer[target-id="engagement-panel-searchable-transcript"]'
                );
                debug.method3_panelFound = !!newPanel;
                debug.method3_hasTimestamps = newPanel ? /\\d+:\\d{2}/.test(newPanel.innerText) : false;
                if (newPanel && /\\d+:\\d{2}/.test(newPanel.innerText)) {
                    debug.method3_result = 'SUCCESS: ' + newPanel.innerText.slice(0, 200);
                    return debug;
                }
                debug.method3_result = 'FAIL: panel not found or no timestamps';

                // Method 4: Legacy - old visibility attribute
                const panels = document.querySelectorAll('ytd-engagement-panel-section-list-renderer');
                debug.method4_panels = [];
                for (const p of panels) {
                    const info = {
                        targetId: p.getAttribute('target-id'),
                        visibility: p.getAttribute('visibility'),
                        hasTimestamps: /\\d+:\\d{2}/.test(p.innerText),
                        textPreview: p.innerText?.slice(0, 100)
                    };
                    debug.method4_panels.push(info);

                    if (p.getAttribute('visibility') === 'ENGAGEMENT_PANEL_VISIBILITY_EXPANDED'
                        && /\\d+:\\d{2}/.test(p.innerText)) {
                        debug.method4_result = 'SUCCESS: ' + p.innerText.slice(0, 200);
                        return debug;
                    }
                }
                debug.method4_result = 'FAIL: no expanded panel with timestamps';

                return debug;
            }
        ''')

        print(json.dumps(result, indent=2, ensure_ascii=False))

        await context.close()
        print("\n=== Debug complete ===")

if __name__ == "__main__":
    asyncio.run(main())
