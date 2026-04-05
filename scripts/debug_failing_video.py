#!/usr/bin/env python3
"""
Debug script to analyze why transcript extraction fails for specific videos.
Following user's manual path:
1. Wait for page to fully load
2. Click "...more" button in description
3. Find "Transcript" section and click "Show transcript"
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
    print(f"CDP: {cdp_url[:60]}...")

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(cdp_url)
        context = await browser.new_context(viewport={"width": 1280, "height": 720})
        page = await context.new_page()

        print(f"\n=== Step 1: Navigate and wait for full load ===")
        await page.goto(url, wait_until="load")
        await page.wait_for_timeout(3000)

        # Check page state
        state = await page.evaluate('''() => ({
            title: document.title,
            hasVideo: !!document.querySelector('video'),
            hasDescription: !!document.querySelector('#description, ytd-text-inline-expander'),
            descriptionExpanded: document.querySelector('ytd-text-inline-expander')?.hasAttribute('is-expanded'),
        })''')
        print(f"Page state: {state}")

        print(f"\n=== Step 2: Find and click '...more' button ===")
        # Check what expand buttons exist
        expand_info = await page.evaluate('''() => {
            const results = [];
            // Check for "...more" or "more" text
            const allElements = document.querySelectorAll('*');
            for (const el of allElements) {
                const text = el.textContent?.trim();
                if (text && (text === '...more' || text === 'more' || text === 'Show more')) {
                    results.push({
                        tag: el.tagName,
                        text: text,
                        id: el.id,
                        className: el.className?.slice(0, 50),
                        visible: el.offsetParent !== null,
                        clickable: el.tagName === 'BUTTON' || el.tagName === 'A' || el.getAttribute('role') === 'button'
                    });
                }
            }
            // Also check tp-yt-paper-button#expand
            const expandBtn = document.querySelector('tp-yt-paper-button#expand');
            if (expandBtn) {
                results.push({
                    tag: 'tp-yt-paper-button',
                    id: 'expand',
                    text: expandBtn.textContent?.trim().slice(0, 30),
                    visible: expandBtn.offsetParent !== null,
                    hidden: expandBtn.hasAttribute('hidden')
                });
            }
            return results;
        }''')
        print(f"Expand elements found: {json.dumps(expand_info, indent=2)}")

        # Try to click the expand button
        expand_clicked = await page.evaluate('''() => {
            // Method 1: tp-yt-paper-button#expand
            const expandBtn = document.querySelector('tp-yt-paper-button#expand:not([hidden])');
            if (expandBtn && expandBtn.offsetParent !== null) {
                expandBtn.scrollIntoView({ block: 'center' });
                expandBtn.click();
                return 'tp-yt-paper-button#expand';
            }
            // Method 2: Find "...more" text and click parent
            const moreSpan = Array.from(document.querySelectorAll('span, yt-formatted-string')).find(
                el => el.textContent?.trim() === '...more' || el.textContent?.trim() === 'more'
            );
            if (moreSpan) {
                const clickable = moreSpan.closest('button, [role="button"], tp-yt-paper-button, a');
                if (clickable) {
                    clickable.scrollIntoView({ block: 'center' });
                    clickable.click();
                    return 'more-span-parent';
                }
                // Try clicking the span itself
                moreSpan.click();
                return 'more-span-direct';
            }
            // Method 3: Click on description expander
            const expander = document.querySelector('ytd-text-inline-expander #expand, #description-inline-expander #expand');
            if (expander) {
                expander.scrollIntoView({ block: 'center' });
                expander.click();
                return 'expander-element';
            }
            return null;
        }''')
        print(f"Expand clicked via: {expand_clicked}")
        await page.wait_for_timeout(1500)

        print(f"\n=== Step 3: Look for Transcript section ===")
        # After expanding, check what's visible
        transcript_info = await page.evaluate('''() => {
            const results = {
                transcriptSection: null,
                showTranscriptBtn: null,
                transcriptText: [],
                descriptionContent: null
            };

            // Check for transcript section renderer
            const transcriptSection = document.querySelector('ytd-video-description-transcript-section-renderer');
            if (transcriptSection) {
                results.transcriptSection = {
                    exists: true,
                    visible: transcriptSection.offsetParent !== null,
                    innerHTML: transcriptSection.innerHTML?.slice(0, 200)
                };
                // Find button inside
                const btn = transcriptSection.querySelector('button, [role="button"]');
                if (btn) {
                    results.showTranscriptBtn = {
                        tag: btn.tagName,
                        text: btn.textContent?.trim(),
                        ariaLabel: btn.getAttribute('aria-label'),
                        visible: btn.offsetParent !== null
                    };
                }
            }

            // Search for "Show transcript" button anywhere
            const allBtns = document.querySelectorAll('button, [role="button"]');
            for (const btn of allBtns) {
                const label = btn.getAttribute('aria-label') || '';
                const text = btn.textContent?.trim() || '';
                if (label.toLowerCase().includes('transcript') || text.toLowerCase().includes('transcript')) {
                    results.transcriptText.push({
                        tag: btn.tagName,
                        text: text.slice(0, 50),
                        ariaLabel: label,
                        visible: btn.offsetParent !== null
                    });
                }
            }

            // Check expanded description for "Transcript" text
            const desc = document.querySelector('#description-inner, ytd-text-inline-expander #plain-snippet-text');
            if (desc) {
                const text = desc.innerText || '';
                const transcriptIndex = text.toLowerCase().indexOf('transcript');
                if (transcriptIndex !== -1) {
                    results.descriptionContent = text.slice(Math.max(0, transcriptIndex - 50), transcriptIndex + 100);
                }
            }

            return results;
        }''')
        print(f"Transcript info: {json.dumps(transcript_info, indent=2)}")

        # Take screenshot
        print(f"\n=== Taking screenshot ===")
        await page.screenshot(path=f"/tmp/debug_{VIDEO_ID}.png", full_page=False)
        print(f"Screenshot saved to /tmp/debug_{VIDEO_ID}.png")

        # Try clicking Show transcript if found
        if transcript_info.get('showTranscriptBtn') or transcript_info.get('transcriptText'):
            print(f"\n=== Step 4: Try clicking Show transcript ===")
            click_result = await page.evaluate('''() => {
                // Method 1: aria-label
                const btn1 = document.querySelector('[aria-label="Show transcript"]');
                if (btn1 && btn1.offsetParent !== null) {
                    btn1.scrollIntoView({ block: 'center' });
                    btn1.click();
                    return 'aria-label';
                }
                // Method 2: transcript section button
                const section = document.querySelector('ytd-video-description-transcript-section-renderer');
                if (section) {
                    const btn = section.querySelector('button, [role="button"]');
                    if (btn) {
                        btn.scrollIntoView({ block: 'center' });
                        btn.click();
                        return 'section-button';
                    }
                }
                // Method 3: any button with transcript text
                const btns = document.querySelectorAll('button');
                for (const btn of btns) {
                    if (btn.textContent?.toLowerCase().includes('show transcript')) {
                        btn.scrollIntoView({ block: 'center' });
                        btn.click();
                        return 'text-match';
                    }
                }
                return null;
            }''')
            print(f"Click result: {click_result}")
            await page.wait_for_timeout(2000)

            # Check if transcript panel opened
            panel_state = await page.evaluate('''() => {
                const segments = document.querySelectorAll('transcript-segment-view-model');
                const panels = document.querySelectorAll('ytd-engagement-panel-section-list-renderer');
                let expandedPanel = null;
                for (const p of panels) {
                    if (p.getAttribute('visibility') === 'ENGAGEMENT_PANEL_VISIBILITY_EXPANDED') {
                        expandedPanel = {
                            targetId: p.getAttribute('target-id'),
                            hasTimestamps: /\\d+:\\d{2}/.test(p.innerText)
                        };
                        break;
                    }
                }
                return {
                    segmentCount: segments.length,
                    expandedPanel: expandedPanel
                };
            }''')
            print(f"Panel state after click: {panel_state}")

        await context.close()
        print("\n=== Debug complete ===")

if __name__ == "__main__":
    asyncio.run(main())
