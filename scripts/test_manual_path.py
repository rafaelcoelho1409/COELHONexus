#!/usr/bin/env python3
"""
Test the exact manual path the user described:
1. Wait page until it's fully loaded
2. Click on "...more" button in description section
3. Go to "Transcript" subheader, click "Show transcript" button
4. Extract transcript content
"""
import asyncio
import json
from playwright.async_api import async_playwright

CDP_HEADED = "https://playwright-cdp.YOUR_TAILNET_DOMAIN.ts.net"
VIDEO_ID = "wGJMlkBLBRI"  # One of the failing videos

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
    print(f"URL: {url}")

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(cdp_url)
        context = await browser.new_context(viewport={"width": 1280, "height": 720})
        page = await context.new_page()

        # Step 1: Wait for page to fully load
        print("\n=== Step 1: Wait for page to fully load ===")
        await page.goto(url, wait_until="load")
        await page.wait_for_timeout(3000)
        print("Page loaded, waiting 3s for dynamic content...")

        # Step 2: Click "...more" button in description
        print("\n=== Step 2: Click '...more' button ===")
        expand_result = await page.evaluate('''() => {
            const btn = document.querySelector('tp-yt-paper-button#expand:not([hidden])');
            if (btn && btn.offsetParent !== null) {
                btn.scrollIntoView({ block: 'center' });
                btn.click();
                return { success: true, method: 'tp-yt-paper-button#expand' };
            }
            return { success: false, error: 'Button not found or not visible' };
        }''')
        print(f"Expand result: {expand_result}")

        if not expand_result.get('success'):
            print("ERROR: Could not click expand button!")
            await context.close()
            return

        await page.wait_for_timeout(1500)
        print("Waited 1.5s for description to expand...")

        # Step 3: Find and click "Show transcript" button
        print("\n=== Step 3: Scroll to Transcript section and click 'Show transcript' ===")

        # First scroll to transcript section
        scroll_result = await page.evaluate('''() => {
            const section = document.querySelector('ytd-video-description-transcript-section-renderer');
            if (section) {
                section.scrollIntoView({ block: 'center', behavior: 'smooth' });
                return { found: true };
            }
            return { found: false };
        }''')
        print(f"Transcript section scroll: {scroll_result}")
        await page.wait_for_timeout(500)

        # Now click the button
        click_result = await page.evaluate('''() => {
            const btn = document.querySelector('[aria-label="Show transcript"]');
            if (btn && btn.offsetParent !== null) {
                btn.scrollIntoView({ block: 'center' });
                btn.click();
                return { success: true, method: 'aria-label', text: btn.textContent?.trim() };
            }
            // Fallback: find button in transcript section
            const section = document.querySelector('ytd-video-description-transcript-section-renderer');
            if (section) {
                const sectionBtn = section.querySelector('button');
                if (sectionBtn && sectionBtn.offsetParent !== null) {
                    sectionBtn.scrollIntoView({ block: 'center' });
                    sectionBtn.click();
                    return { success: true, method: 'section-button', text: sectionBtn.textContent?.trim() };
                }
            }
            return { success: false, error: 'Show transcript button not found' };
        }''')
        print(f"Click result: {click_result}")

        if not click_result.get('success'):
            print("ERROR: Could not click Show transcript button!")
            await context.close()
            return

        # Wait for transcript panel to load
        print("\n=== Step 4: Wait for transcript panel and extract content ===")
        await page.wait_for_timeout(3000)
        print("Waited 3s for panel to load...")

        # Check panel state
        panel_state = await page.evaluate('''() => {
            const panel = document.querySelector(
                'ytd-engagement-panel-section-list-renderer[visibility="ENGAGEMENT_PANEL_VISIBILITY_EXPANDED"]'
            );
            if (!panel) return { error: 'No expanded panel found' };

            return {
                targetId: panel.getAttribute('target-id'),
                segmentModels: panel.querySelectorAll('transcript-segment-view-model').length,
                segmentRenderers: panel.querySelectorAll('ytd-transcript-segment-renderer').length,
                segmentTexts: panel.querySelectorAll('.segment-text').length,
                hasTimestamps: /\\d+:\\d{2}/.test(panel.innerText),
                innerTextLength: panel.innerText?.length || 0,
                innerTextPreview: panel.innerText?.slice(0, 300)
            };
        }''')
        print(f"Panel state: {json.dumps(panel_state, indent=2, ensure_ascii=False)}")

        # Extract transcript
        print("\n=== Step 5: Extract transcript text ===")
        transcript = await page.evaluate('''() => {
            // Try .segment-text elements first (old UI)
            const segmentTexts = document.querySelectorAll(
                'ytd-engagement-panel-section-list-renderer[visibility="ENGAGEMENT_PANEL_VISIBILITY_EXPANDED"] .segment-text'
            );
            if (segmentTexts.length > 0) {
                const parts = [];
                segmentTexts.forEach(el => {
                    const container = el.closest('ytd-transcript-segment-renderer');
                    const timestamp = container?.querySelector('.segment-timestamp')?.innerText?.trim() || '';
                    const text = el.innerText?.trim() || '';
                    if (timestamp && text) {
                        parts.push(timestamp + '\\n' + text);
                    }
                });
                return { method: 'segment-text', count: segmentTexts.length, text: parts.join('\\n') };
            }

            // Try transcript-segment-view-model (new UI)
            const segmentModels = document.querySelectorAll('transcript-segment-view-model');
            if (segmentModels.length > 0) {
                const parts = [];
                segmentModels.forEach(seg => {
                    parts.push(seg.innerText?.trim() || '');
                });
                return { method: 'segment-view-model', count: segmentModels.length, text: parts.join('\\n') };
            }

            // Fallback: get panel innerText
            const panel = document.querySelector(
                'ytd-engagement-panel-section-list-renderer[visibility="ENGAGEMENT_PANEL_VISIBILITY_EXPANDED"]'
            );
            if (panel && /\\d+:\\d{2}/.test(panel.innerText)) {
                return { method: 'panel-innerText', count: 0, text: panel.innerText };
            }

            return { error: 'No transcript content found' };
        }''')

        if 'error' in transcript:
            print(f"ERROR: {transcript['error']}")
        else:
            print(f"Method: {transcript['method']}")
            print(f"Segment count: {transcript['count']}")
            print(f"Text length: {len(transcript.get('text', ''))}")
            print(f"Text preview:\n{transcript.get('text', '')[:500]}...")

        await context.close()
        print("\n=== Test complete ===")

if __name__ == "__main__":
    asyncio.run(main())
