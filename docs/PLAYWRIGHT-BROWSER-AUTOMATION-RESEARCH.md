# Playwright Browser Automation Research

## Overview

This document covers research on using Playwright for web scraping, specifically for YouTube transcript extraction when direct API methods are blocked by IP restrictions.

**Date**: 2026-03-27

---

## The Problem

YouTube blocks transcript/caption API requests from:
- Cloud provider IPs (AWS, GCP, Azure, k3d clusters)
- VPN IPs (including Cloudflare WARP)
- Tor exit nodes
- Datacenter IPs

**Current status in COELHONexus:**
- `youtube-transcript-api` blocked with `IpBlocked` / `RequestBlocked`
- `yt-dlp --write-auto-subs` blocked with HTTP 429
- WARP proxy: Rate-limited (works sporadically)
- Tor proxy: Blocked

**Solution explored**: Browser automation with Playwright to mimic real user behavior.

---

## Browser-as-a-Service Comparison

### Proxy Support Matrix

| Service | SOCKS5 (Tor) | HTTP (WARP) | Helm Chart | License |
|---------|--------------|-------------|------------|---------|
| **Playwright** | YES | YES | No official | Apache 2.0 |
| **Steel Browser** | YES | YES | No | Open-source |
| **Selenium Grid** | YES | YES | Official | Apache 2.0 |
| **Browserless** | NO | YES | Community | SSPL |
| **Splash** | YES | YES | Community | BSD |

### Feature Comparison

| Feature | Playwright | Browserless | Selenium Grid |
|---------|------------|-------------|---------------|
| Multi-browser | Chromium, Firefox, WebKit | Chrome only | All browsers |
| Official support | Microsoft | Third-party | Selenium HQ |
| Server mode | `run-server` | Native | Grid Hub |
| VNC access | Community images | No | Built-in |
| K8s scaling | Manual | HPA | Dynamic Grid |

---

## Official Playwright Docker Images

### Microsoft Container Registry Images

| Variant | Image | Size |
|---------|-------|------|
| **Node.js** | `mcr.microsoft.com/playwright:v1.58.2-noble` | ~2GB |
| **Python** | `mcr.microsoft.com/playwright/python:v1.58.0-noble` | ~2GB |
| **Java** | `mcr.microsoft.com/playwright/java:latest` | ~2GB |
| **.NET** | `mcr.microsoft.com/playwright/dotnet:latest` | ~2GB |

### Available Tags

- **Ubuntu versions**: `noble` (24.04), `jammy` (22.04), `focal` (20.04)
- **Architecture**: `-amd64`, `-arm64`
- **Development**: `latest`, `next`, `dev`

### Important Notes

- Images include browsers and system dependencies
- Images do NOT include the Playwright package itself
- Always pin to specific version to avoid browser/package mismatches

---

## Playwright Server Mode

Playwright can run as a remote WebSocket server:

### Running the Server

```bash
# Docker
docker run -p 3000:3000 --rm --init \
  mcr.microsoft.com/playwright:v1.58.2-noble \
  npx playwright run-server --port 3000 --host 0.0.0.0

# Or with Python image
docker run -p 3000:3000 --rm --init \
  mcr.microsoft.com/playwright/python:v1.58.0-noble \
  npx playwright run-server --port 3000 --host 0.0.0.0
```

### Connecting from Client

```python
from playwright.async_api import async_playwright

async with async_playwright() as p:
    # Connect to remote Playwright server
    browser = await p.chromium.connect("ws://playwright-server:3000")
    page = await browser.new_page()
    await page.goto("https://youtube.com")
```

---

## Proxy Configuration (SOCKS5 for Tor/WARP)

### Browser-Level Proxy

```python
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    # Tor SOCKS5
    browser = p.chromium.launch(
        proxy={"server": "socks5://127.0.0.1:9050"}
    )

    # WARP SOCKS5 with authentication
    browser = p.chromium.launch(
        proxy={
            "server": "socks5://warp-proxy:1080",
            "username": "user",
            "password": "password"
        }
    )
```

### Context-Level Proxy (Different Proxies per Session)

```python
browser = p.chromium.launch()

# Context 1: Through Tor
context_tor = browser.new_context(
    proxy={"server": "socks5://127.0.0.1:9050"}
)

# Context 2: Through WARP
context_warp = browser.new_context(
    proxy={"server": "socks5://warp-proxy:1080"}
)

# Context 3: Direct (no proxy)
context_direct = browser.new_context()
```

### Async with Multiple Proxies

```python
import asyncio
from playwright.async_api import async_playwright

PROXIES = {
    "tor": "socks5://127.0.0.1:9050",
    "warp": "socks5://warp-proxy:1080",
    "direct": None
}

async def create_session(browser, proxy_type: str):
    proxy_config = {"server": PROXIES[proxy_type]} if PROXIES[proxy_type] else None
    context = await browser.new_context(proxy=proxy_config)
    return context

async def run_concurrent():
    async with async_playwright() as p:
        browser = await p.chromium.launch()

        # Create 10 sessions with different proxies
        contexts = await asyncio.gather(*[
            create_session(browser, "tor") for _ in range(10)
        ])

        # Use contexts...
        await browser.close()
```

---

## VNC/Visual Debugging

### Playwright vs Selenium Grid

| Feature | Selenium Grid | Playwright |
|---------|---------------|------------|
| Native VNC | Built-in official | Not built-in |
| noVNC web viewer | Official support | Community only |
| Debug experience | VNC streaming | Trace Viewer |
| Video recording | Via VNC | Built-in native |

### Community VNC Images

| Image | Description |
|-------|-------------|
| `digitronik/playwright-vnc` | Playwright with VNC for visual monitoring |
| `land007/playwright-novnc` | Playwright with noVNC web viewer |
| `xtr-dev/mcp-playwright-novnc` | Playwright MCP server with X11/noVNC |

### Playwright's Alternative Debugging Tools

**Trace Viewer** (recommended for production):
```python
context = browser.new_context()
context.tracing.start(screenshots=True, snapshots=True)

# ... your automation code ...

context.tracing.stop(path="trace.zip")
# View with: npx playwright show-trace trace.zip
```

**Video Recording**:
```python
context = browser.new_context(record_video_dir="videos/")
# Videos saved automatically per page
```

---

## Language Performance: Python vs Go vs Rust

### Critical Architecture Fact

**ALL Playwright language bindings spawn a Node.js subprocess:**

```
Your Code → JSON-RPC/stdio → Node.js Playwright Server → Browser
                              ↑ Identical for ALL languages
```

### Performance Comparison

| Aspect | Python | Go | Rust |
|--------|--------|-----|------|
| **Official support** | Microsoft | Community | Experimental |
| **Production-ready** | Yes | Limited | No |
| **Feature parity** | 100% | ~95% | ~70% |
| **Runtime overhead** | ~30-50MB | ~10-20MB | ~5-10MB |
| **Node.js subprocess** | ~50-80MB | ~50-80MB | ~50-80MB |
| **Browser per context** | 50-100MB | 50-100MB | 50-100MB |

### Memory Breakdown (50 concurrent sessions)

| Component | Memory |
|-----------|--------|
| Browser contexts (50x) | 2.5-5GB |
| Node.js subprocess | 50-80MB |
| Language runtime | 10-50MB |
| **Total** | ~2.6-5.2GB |

**Language overhead is ~5-10% of total memory at scale.**

### Recommendation

**Stick with Python** because:
1. Official Microsoft support = immediate bug fixes
2. 100% feature parity
3. Go/Rust savings are marginal (5-10%)
4. `playwright-rust` is "not yet ready for production"

**Consider Go/Rust only when:**
- Running 500+ concurrent sessions
- Existing Go/Rust infrastructure with no Python expertise

---

## Helm Charts Available

### ArtifactHub Options

| Chart | Version | App Version | Notes |
|-------|---------|-------------|-------|
| **browserless** (icoretech) | 0.5.0 | v2.46.0 | Best maintained, HPA support |
| **browserless** (victorlane) | 0.2.0 | v2.43.0 | Basic features |
| **mcp-playwright** (mcp-helm) | 0.1.1 | - | Playwright MCP server |

**Note**: Microsoft does not provide an official Playwright Helm chart.

### Browserless Limitation

Browserless does **NOT support SOCKS5** proxies (only HTTP/HTTPS). For Tor support, use native Playwright images.

---

## YouTube Transcript Extraction with Playwright

### Approach: Request Interception

The most reliable method intercepts YouTube's internal API calls:

```python
from playwright.async_api import async_playwright

async def extract_transcript(video_id: str, proxy: str = None):
    async with async_playwright() as p:
        launch_opts = {}
        if proxy:
            launch_opts["proxy"] = {"server": proxy}

        browser = await p.chromium.launch(**launch_opts)
        context = await browser.new_context()
        page = await context.new_page()

        transcript_data = None

        # Intercept transcript API calls
        async def handle_route(route):
            nonlocal transcript_data
            response = await route.fetch()
            if "get_transcript" in route.request.url or "timedtext" in route.request.url:
                transcript_data = await response.json()
            await route.fulfill(response=response)

        await page.route("**/*", handle_route)

        # Navigate to video
        await page.goto(f"https://www.youtube.com/watch?v={video_id}")
        await page.wait_for_timeout(3000)

        # Click "Show transcript" button
        try:
            await page.click('[aria-label*="transcript" i]')
            await page.wait_for_timeout(2000)

            # Extract transcript segments
            segments = await page.query_selector_all('ytd-transcript-segment-renderer')
            transcript_text = []
            for segment in segments:
                text = await segment.inner_text()
                transcript_text.append(text)

            return "\n".join(transcript_text)
        except Exception as e:
            return None
        finally:
            await browser.close()
```

### Success Factors

1. **Stealth mode**: Use `--disable-blink-features=AutomationControlled`
2. **Real user agent**: Spoof Chrome on Windows/Linux
3. **Human-like delays**: 2-5 seconds between actions
4. **Request interception**: Capture auth headers from real browser session

---

## Deployment Architecture Options

### Option 1: 24/7 Pod (Not Recommended for Batch Jobs)

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: playwright
spec:
  replicas: 1
  template:
    spec:
      containers:
      - name: playwright
        image: mcr.microsoft.com/playwright:v1.58.2-noble
        command: ["npx", "playwright", "run-server", "--port", "3000"]
```

**When to use**: Real-time scraping API, continuous monitoring

### Option 2: Kubernetes Job (Recommended for Batch)

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: transcript-extraction
spec:
  template:
    spec:
      containers:
      - name: playwright
        image: mcr.microsoft.com/playwright/python:v1.58.0-noble
        command: ["python", "extract_transcripts.py"]
        env:
        - name: TOR_PROXY
          value: "socks5://tor-proxy:9050"
      restartPolicy: Never
  backoffLimit: 3
```

**When to use**: On-demand transcript extraction, scheduled jobs

### Option 3: Scale-to-Zero with KEDA

```yaml
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata:
  name: playwright-scaler
spec:
  scaleTargetRef:
    name: playwright
  minReplicaCount: 0
  maxReplicaCount: 5
  triggers:
  - type: prometheus
    metadata:
      query: rate(transcript_requests_total[1m])
      threshold: "1"
```

**When to use**: API must be always available but want zero idle cost

### Cost Comparison

| Approach | Idle Cost | Cold Start | Best For |
|----------|-----------|------------|----------|
| 24/7 Pod | High | None | Real-time API |
| K8s Job | Zero | N/A (batch) | Scheduled jobs |
| KEDA | Zero | 30-60s | On-demand API |

---

## Recommendations for COELHONexus

### Short-term (Testing)
1. Use Kubernetes Job for batch transcript extraction
2. Connect through existing Tor/WARP proxy infrastructure
3. Test with small batches to validate approach

### Medium-term (Production)
1. Implement KEDA scale-to-zero for cost efficiency
2. Add request interception for reliable transcript capture
3. Cache transcripts in ElasticSearch to avoid re-extraction

### Long-term (Scale)
1. Consider residential proxy service if Tor/WARP blocking persists
2. Implement browser pool with context reuse
3. Add Trace Viewer integration for debugging

---

## References

### Official Documentation
- [Playwright Docker](https://playwright.dev/docs/docker)
- [Playwright Network/Proxy](https://playwright.dev/python/docs/network)
- [Playwright BrowserServer API](https://playwright.dev/docs/api/class-browserserver)
- [Microsoft Container Registry - Playwright](https://mcr.microsoft.com/en-us/product/playwright/about)

### GitHub Issues
- [Playwright Helm Chart Request #35676](https://github.com/microsoft/playwright/issues/35676)
- [Tor Proxy Support #1709](https://github.com/microsoft/playwright-python/issues/1709)
- [SOCKS5 Proxy #324](https://github.com/microsoft/playwright-python/issues/324)

### Community Resources
- [playwright-community/playwright-go](https://github.com/mxschmitt/playwright-go)
- [octaltree/playwright-rust](https://github.com/octaltree/playwright-rust)
- [Browserless Documentation](https://docs.browserless.io/)

### Helm Charts
- [icoretech/browserless](https://artifacthub.io/packages/helm/icoretech/browserless)
- [mcp-playwright](https://artifacthub.io/packages/helm/mcp-helm/mcp-playwright)
