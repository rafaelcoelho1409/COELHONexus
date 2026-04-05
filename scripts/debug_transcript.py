#!/usr/bin/env python3
"""
Debug script to inspect YouTube transcript DOM structure.
Connects to Playwright CDP server and explores the transcript panel.
"""
import asyncio
import json
from playwright.async_api import async_playwright

CDP_HEADED = "https://playwright-cdp.YOUR_TAILNET_DOMAIN.ts.net"  # Headed browser (required for transcripts)
VIDEO_ID = "lX4S3_wAi-4"  # Video that returned empty content despite having transcript

def get_cdp_url(endpoint: str) -> str:
    """Get WebSocket URL from CDP endpoint (handles HTTPS -> WSS)."""
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

        # Convert ws:// to wss:// if endpoint is https://
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

        # Step 1: Expand description
        print("\n=== Step 1: Expand description ===")
        try:
            expand_btn = page.locator('tp-yt-paper-button#expand:not([hidden])').first
            if await expand_btn.count() > 0:
                await expand_btn.click(timeout=3000)
                print("Description expanded")
                await page.wait_for_timeout(1000)
        except Exception as e:
            print(f"Expand failed: {e}")

        # Step 2: Click "Show transcript" button
        print("\n=== Step 2: Click Show transcript ===")
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
        await page.wait_for_timeout(2000)

        # Step 3: Inspect the panel structure
        print("\n=== Step 3: Panel structure after clicking transcript button ===")
        panels_info = await page.evaluate('''
            () => {
                const panels = document.querySelectorAll('ytd-engagement-panel-section-list-renderer');
                return Array.from(panels).map(p => ({
                    targetId: p.getAttribute('target-id'),
                    visibility: p.getAttribute('visibility'),
                    innerHTML_length: p.innerHTML.length,
                    // Check for tabs
                    tabs: Array.from(p.querySelectorAll('button, [role="tab"]')).map(t => ({
                        text: t.textContent?.trim().slice(0, 30),
                        ariaSelected: t.getAttribute('aria-selected'),
                        tagName: t.tagName,
                    })),
                    // Check for segment containers
                    transcriptRenderer: !!p.querySelector('ytd-transcript-renderer'),
                    transcriptSearchPanel: !!p.querySelector('ytd-transcript-search-panel-renderer'),
                    segmentList: !!p.querySelector('ytd-transcript-segment-list-renderer'),
                    segments: p.querySelectorAll('ytd-transcript-segment-renderer, transcript-segment-view-model, .segment-text').length,
                }));
            }
        ''')
        for i, panel in enumerate(panels_info):
            if panel.get('targetId') and 'transcript' in str(panel.get('targetId', '')).lower():
                print(f"\nPanel {i}: {panel['targetId']}")
                print(f"  visibility: {panel['visibility']}")
                print(f"  tabs: {panel['tabs']}")
                print(f"  transcriptRenderer: {panel['transcriptRenderer']}")
                print(f"  transcriptSearchPanel: {panel['transcriptSearchPanel']}")
                print(f"  segmentList: {panel['segmentList']}")
                print(f"  segments: {panel['segments']}")

        # Step 4: Find and click the actual Transcript tab
        print("\n=== Step 4: Looking for clickable transcript tab ===")
        tab_info = await page.evaluate('''
            () => {
                // Find the expanded panel
                const panel = document.querySelector(
                    'ytd-engagement-panel-section-list-renderer[visibility="ENGAGEMENT_PANEL_VISIBILITY_EXPANDED"]'
                );
                if (!panel) return { error: 'No expanded panel' };

                // Look for tab-like elements
                const candidates = [];

                // Method 1: yt-tab-shape elements
                const ytTabs = panel.querySelectorAll('yt-tab-shape, yt-chip-cloud-chip-renderer');
                for (const tab of ytTabs) {
                    candidates.push({
                        type: 'yt-tab-shape',
                        text: tab.textContent?.trim().slice(0, 30),
                        tagName: tab.tagName,
                        isTranscript: tab.textContent?.toLowerCase().includes('transcript'),
                    });
                }

                // Method 2: button with role=tab
                const roleTabs = panel.querySelectorAll('[role="tab"]');
                for (const tab of roleTabs) {
                    candidates.push({
                        type: 'role-tab',
                        text: tab.textContent?.trim().slice(0, 30),
                        ariaSelected: tab.getAttribute('aria-selected'),
                        isTranscript: tab.textContent?.toLowerCase().includes('transcript'),
                    });
                }

                // Method 3: Any clickable with "Transcript" text
                const allClickable = panel.querySelectorAll('button, a, [role="button"], [role="tab"], yt-formatted-string');
                for (const el of allClickable) {
                    if (el.textContent?.trim().toLowerCase() === 'transcript') {
                        candidates.push({
                            type: 'text-match',
                            text: el.textContent?.trim(),
                            tagName: el.tagName,
                            className: el.className?.slice(0, 50),
                            parentTag: el.parentElement?.tagName,
                        });
                    }
                }

                return { candidates };
            }
        ''')
        print(f"Tab candidates: {json.dumps(tab_info, indent=2)}")

        # Step 5: Try clicking the Transcript text element directly
        print("\n=== Step 5: Click Transcript tab/text ===")
        click_result = await page.evaluate('''
            () => {
                const panel = document.querySelector(
                    'ytd-engagement-panel-section-list-renderer[visibility="ENGAGEMENT_PANEL_VISIBILITY_EXPANDED"]'
                );
                if (!panel) return { error: 'No expanded panel' };

                // Find elements containing "Transcript" text
                const walker = document.createTreeWalker(
                    panel,
                    NodeFilter.SHOW_ELEMENT,
                    null
                );

                let clicked = null;
                let node;
                while (node = walker.nextNode()) {
                    const text = node.textContent?.trim();
                    if (text === 'Transcript' && node.offsetParent !== null) {
                        // This element has exactly "Transcript" text and is visible
                        console.log('Found Transcript element:', node.tagName, node.className);
                        node.click();
                        clicked = {
                            tagName: node.tagName,
                            className: node.className?.slice(0, 50),
                            text: text,
                        };
                        break;
                    }
                }

                return clicked || { error: 'No clickable Transcript element found' };
            }
        ''')
        print(f"Click result: {click_result}")
        await page.wait_for_timeout(2000)

        # Step 6: Check for segments after click
        print("\n=== Step 6: Check for segments after tab click ===")
        segments_info = await page.evaluate('''
            () => {
                const panel = document.querySelector(
                    'ytd-engagement-panel-section-list-renderer[visibility="ENGAGEMENT_PANEL_VISIBILITY_EXPANDED"]'
                );
                if (!panel) return { error: 'No panel' };

                return {
                    transcriptRenderer: !!panel.querySelector('ytd-transcript-renderer'),
                    segmentList: !!panel.querySelector('ytd-transcript-segment-list-renderer'),
                    segmentRenderers: panel.querySelectorAll('ytd-transcript-segment-renderer').length,
                    segmentViewModels: panel.querySelectorAll('transcript-segment-view-model').length,
                    segmentTexts: panel.querySelectorAll('.segment-text').length,
                    // Check innerHTML for timestamps
                    hasTimestamps: /\\d+:\\d{2}/.test(panel.innerText),
                    innerTextPreview: panel.innerText?.slice(0, 500),
                };
            }
        ''')
        print(f"Segments info: {json.dumps(segments_info, indent=2)}")

        # Step 7: Take a screenshot for visual inspection
        print("\n=== Step 7: Taking screenshot ===")
        await page.screenshot(path="/tmp/youtube_transcript_debug.png", full_page=False)
        print("Screenshot saved to /tmp/youtube_transcript_debug.png")

        # Step 8: Get full panel HTML for analysis
        print("\n=== Step 8: Panel HTML structure ===")
        panel_html = await page.evaluate('''
            () => {
                const panel = document.querySelector(
                    'ytd-engagement-panel-section-list-renderer[visibility="ENGAGEMENT_PANEL_VISIBILITY_EXPANDED"]'
                );
                if (!panel) return 'No panel';

                // Get structure without full content
                const getStructure = (el, depth = 0) => {
                    if (depth > 4) return '...';
                    const tag = el.tagName?.toLowerCase() || 'text';
                    const id = el.id ? `#${el.id}` : '';
                    const cls = el.className ? `.${el.className.split(' ')[0]}` : '';
                    const children = Array.from(el.children || []).map(c => getStructure(c, depth + 1));
                    return {
                        tag: `${tag}${id}${cls}`,
                        children: children.length > 0 ? children : undefined,
                    };
                };

                return getStructure(panel);
            }
        ''')
        print(f"Panel structure: {json.dumps(panel_html, indent=2)[:2000]}")

        await context.close()
        print("\n=== Debug complete ===")

if __name__ == "__main__":
    asyncio.run(main())
