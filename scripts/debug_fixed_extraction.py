#!/usr/bin/env python3
"""
Debug script to test fixed extraction logic
"""
import asyncio
import json
import re
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

def parse_transcript(raw_text: str) -> list:
    """Parse raw transcript text into segments."""
    lines = [line.strip() for line in raw_text.split("\n") if line.strip()]
    segments = []
    i = 0
    while i < len(lines) and not re.match(r"^\d+:\d{2}$", lines[i]):
        i += 1
    while i < len(lines):
        line = lines[i]
        if re.match(r"^\d+:\d{2}$", line):
            timestamp = line
            i += 1
            if i < len(lines) and re.match(r"^\d+\s+(second|minute)", lines[i]):
                i += 1
            text_parts = []
            while i < len(lines) and not re.match(r"^\d+:\d{2}$", lines[i]):
                text_parts.append(lines[i])
                i += 1
            if text_parts:
                segments.append({"timestamp": timestamp, "text": " ".join(text_parts)})
        else:
            i += 1
    return segments

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

        # Test FIXED extraction logic
        print("\n=== Testing FIXED extraction logic ===")

        raw_text = await page.evaluate('''
            () => {
                // Method 2: Modern transcript-segment-view-model (Apr 2026 UI)
                const segmentModels = document.querySelectorAll('transcript-segment-view-model');
                if (segmentModels.length > 0) {
                    const parts = [];
                    segmentModels.forEach(seg => {
                        // Extract timestamp from dedicated element
                        const tsEl = seg.querySelector('[class*="Timestamp"], .ytwTranscriptSegmentTimestampContainer div');
                        const textEl = seg.querySelector('.yt-core-attributed-string, [class*="Text"]');
                        const timestamp = tsEl?.innerText?.trim() || '';
                        const text = textEl?.innerText?.trim() || '';
                        if (timestamp && text) {
                            parts.push(timestamp + '\\n' + text);
                        } else if (seg.innerText) {
                            // Fallback: use full innerText (includes timestamp)
                            parts.push(seg.innerText.trim());
                        }
                    });
                    if (parts.length > 0) return parts.join('\\n');
                }
                return '';
            }
        ''')

        print(f"Raw text length: {len(raw_text)}")
        print(f"First 500 chars:\n{raw_text[:500]}")

        # Parse into segments
        segments = parse_transcript(raw_text)
        print(f"\nParsed segments: {len(segments)}")
        for i, seg in enumerate(segments[:5]):
            print(f"  [{seg['timestamp']}] {seg['text'][:60]}...")

        await context.close()
        print("\n=== Debug complete ===")

if __name__ == "__main__":
    asyncio.run(main())
