# YouTube Transcription Research

## Overview

This document covers research on extracting YouTube video transcriptions, bypassing IP blocking, and using NVIDIA NIM for speech-to-text.

**Date**: 2026-03-26

---

## The Problem

YouTube aggressively blocks transcript/caption requests from:
- Cloud provider IPs (AWS, GCP, Azure, k3d clusters)
- VPN IPs (including Cloudflare WARP)
- Tor exit nodes
- Datacenter IPs

**Errors encountered:**
- `IpBlocked` - YouTube blocking cloud IPs
- `RequestBlocked` - Bot detection triggered
- `HTTP 429 Too Many Requests` - Rate limiting
- `Sign in to confirm you're not a bot` - CAPTCHA required

---

## NVIDIA NIM Speech-to-Text Models

### Available Models

| Model | Size | Languages | WER | Speed | Best For |
|-------|------|-----------|-----|-------|----------|
| **Parakeet TDT 0.6B v2** | 600M | English | #1 HuggingFace | Fastest | English real-time |
| **Parakeet TDT 0.6B v3** | 600M | 25 European | 6.32% avg | Fast | Multilingual |
| **Canary 1B v2** | 1B | 24+ langs | 6.67% avg | Medium | Multilingual + translation |
| **Canary-Qwen 2.5B** | 2.5B | Multi | 5.63% (record) | Slower | Maximum accuracy |
| **Whisper Large v3** | 1.5B | 99 langs | Good | Medium | General purpose |

### Recommended Model: Canary 1B v2

**Best for Portuguese + English:**
- Supports both languages with auto-detection
- Built-in translation (Portuguese → English)
- Outperforms Whisper Large v3
- Note: Training data uses European Portuguese (may vary for Brazilian Portuguese)

### Free Tier

- **1,000 free API credits** for NVIDIA Developer Program members
- No credit card required
- Access via `build.nvidia.com`

---

## Python Library for NVIDIA NIM

### Option A: OpenAI Python Client (Recommended)

NVIDIA NIM is OpenAI API-compatible:

```python
from openai import OpenAI
import os

client = OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key=os.environ["NVIDIA_API_KEY"]
)

# Transcribe audio file
with open("audio.mp3", "rb") as f:
    response = client.audio.transcriptions.create(
        model="nvidia/canary-1b-v2",
        file=f
    )
print(response.text)
```

### Option B: HTTP Requests

```python
import requests
import os

url = "https://integrate.api.nvidia.com/v1/audio/transcriptions"
headers = {"Authorization": f"Bearer {os.environ['NVIDIA_API_KEY']}"}
files = {"file": open("audio.mp3", "rb")}
data = {"model": "nvidia/canary-1b-v2"}

response = requests.post(url, headers=headers, files=files, data=data)
print(response.json()["text"])
```

### Supported Audio Formats

- Mono, 16-bit WAV
- OPUS
- FLAC
- MP3

---

## Methods to Bypass YouTube IP Blocking

### Tier 1: FREE Solutions

#### Method 1: Audio Download + NVIDIA NIM (RECOMMENDED)

**Best overall solution - no IP blocking issues.**

```python
import subprocess
import os
from openai import OpenAI

def transcribe_youtube_video(video_id: str) -> str:
    """Download audio and transcribe with NVIDIA NIM."""
    audio_path = f"/tmp/{video_id}.mp3"

    # Step 1: Download audio (NOT blocked by YouTube)
    subprocess.run([
        "yt-dlp", "-x", "--audio-format", "mp3",
        "-o", audio_path,
        f"https://www.youtube.com/watch?v={video_id}"
    ], check=True)

    # Step 2: Transcribe with NVIDIA NIM
    client = OpenAI(
        base_url="https://integrate.api.nvidia.com/v1",
        api_key=os.environ["NVIDIA_API_KEY"]
    )

    with open(audio_path, "rb") as f:
        response = client.audio.transcriptions.create(
            model="nvidia/canary-1b-v2",
            file=f
        )

    # Cleanup
    os.remove(audio_path)

    return response.text
```

**Advantages:**
- yt-dlp audio extraction works (not blocked)
- NVIDIA NIM is free (1000 credits)
- Higher quality than YouTube auto-captions
- Works for ALL videos (even without captions)
- Supports Portuguese + English with auto-detection

#### Method 2: PO Token Provider Plugin (Implemented)

Generates valid "Proof-of-Origin" tokens to bypass bot detection.

**Implementation in COELHONexus:**

The PO Token provider runs as a **sidecar container** in the FastAPI pod:

```yaml
# k8s/helm/templates/fastapi/deployment.yaml
- name: bgutil-pot
  image: jim60105/bgutil-pot:latest
  args: ["server", "--host", "0.0.0.0"]
  ports:
    - containerPort: 4416
  securityContext:
    runAsNonRoot: true
    runAsUser: 1001
    readOnlyRootFilesystem: true
```

**Dependencies:**
```toml
# apps/fastapi/pyproject.toml
"bgutil-ytdlp-pot-provider",  # PO Token provider plugin for yt-dlp
```

**yt-dlp Configuration:**
```python
# apps/fastapi/routers/v1/youtube/helpers.py
BASE_ARGS = [
    ...
    "--extractor-args", "youtubepot-bgutilhttp:base_url=http://127.0.0.1:4416",
]
```

**Pros:** Free, bypasses bot detection, works from cloud IPs, runs as sidecar
**Cons:** Extra container resources (~64-128MB RAM), may break if YouTube changes

#### Method 3: Browser Cookies

Export cookies from a logged-in YouTube browser session.

```bash
# Export with browser extension (e.g., "Get cookies.txt LOCALLY")
# Then use with yt-dlp
yt-dlp --cookies cookies.txt --write-auto-sub VIDEO_URL
```

**Requirements:**
- Cookies must be from same IP used with yt-dlp
- Refresh cookies within 30 minutes
- Risk of account ban if overused

### Tier 2: Paid Solutions

| Method | Reliability | Cost |
|--------|-------------|------|
| Webshare Residential Proxies | Excellent | ~$5/GB |
| Mobile 4G/5G Proxies | Excellent | ~$10/GB |
| BrightData Residential | Excellent | ~$10/GB |

**Webshare integration with youtube-transcript-api:**
```python
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api.proxies import WebshareProxyConfig

proxy_config = WebshareProxyConfig(
    proxy_username="your_username",
    proxy_password="your_password"
)

ytt = YouTubeTranscriptApi(proxy_config=proxy_config)
transcript = ytt.fetch(video_id)
```

---

## Comparison Matrix

| Method | Cost | Reliability | Setup | Works on Cloud |
|--------|------|-------------|-------|----------------|
| **Audio + NVIDIA NIM** | Free | ⭐⭐⭐⭐⭐ | Easy | ✅ Yes |
| PO Token Plugin | Free | ⭐⭐⭐⭐ | Medium | ✅ Yes |
| Browser Cookies | Free | ⭐⭐⭐ | Easy | ⚠️ Maybe |
| WARP Proxy | Free | ⭐⭐ | Easy | ❌ Blocked |
| Tor Proxy | Free | ⭐⭐ | Easy | ❌ Blocked |
| Webshare Residential | Paid | ⭐⭐⭐⭐⭐ | Easy | ✅ Yes |

---

## Recommended Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     YouTube Video Processing                     │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────────┐   │
│  │   yt-dlp     │───▶│   Audio      │───▶│  NVIDIA NIM      │   │
│  │  (metadata)  │    │   Download   │    │  Canary 1B v2    │   │
│  │    ✅ OK     │    │    ✅ OK     │    │     ✅ OK        │   │
│  └──────────────┘    └──────────────┘    └──────────────────┘   │
│         │                                        │               │
│         │                                        │               │
│         ▼                                        ▼               │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                    ElasticSearch                          │   │
│  │  - Video metadata (title, description, channel, etc.)     │   │
│  │  - Full transcription text                                │   │
│  │  - Transcription language                                 │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Implementation Notes

### Audio File Size Considerations

- YouTube videos can be long (1-3 hours)
- Audio files can be 50-200MB
- NVIDIA NIM has file size limits
- Consider chunking long audio files

### Async Processing

```python
import asyncio
from concurrent.futures import ThreadPoolExecutor

async def transcribe_batch(video_ids: list[str]) -> list[str]:
    """Transcribe multiple videos in parallel."""
    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor(max_workers=5) as executor:
        tasks = [
            loop.run_in_executor(executor, transcribe_youtube_video, vid)
            for vid in video_ids
        ]
        return await asyncio.gather(*tasks)
```

### Error Handling

```python
def transcribe_with_fallback(video_id: str) -> dict:
    """Try NVIDIA NIM first, fall back to existing captions."""
    try:
        # Primary: NVIDIA NIM transcription
        text = transcribe_youtube_video(video_id)
        return {"text": text, "source": "nvidia_nim", "language": "auto"}
    except Exception as e:
        # Fallback: Try youtube-transcript-api (may be blocked)
        try:
            from youtube_transcript_api import YouTubeTranscriptApi
            transcript = YouTubeTranscriptApi().fetch(video_id)
            text = " ".join([s.text for s in transcript])
            return {"text": text, "source": "youtube_captions", "language": transcript.language_code}
        except:
            return {"text": "", "error": str(e), "source": "failed"}
```

---

## Third-Party Transcript APIs (Free Tiers)

**Date Added**: 2026-03-27

These services handle IP blocking complexity internally:

| Service | Free Tier | URL |
|---------|-----------|-----|
| **Scrapingdog** | 1,000 credits | https://www.scrapingdog.com/youtube-transcripts-api/ |
| **Supadata** | 100 requests | https://supadata.ai/youtube-transcript-api |
| **TranscriptAPI.com** | 100 credits | https://transcriptapi.com/ |
| **SocialKit** | 20/month | https://www.socialkit.dev/youtube-transcript-api |
| **youtube-transcript.io** | 25 tokens/month | https://www.youtube-transcript.io/pricing |
| **TubeScript** | 2/day no signup | https://tubescript.cc/youtube-transcript-api |

### Key Advantages
- No IP blocking issues (they handle proxies internally)
- No video duration limits (extract existing captions)
- Simple REST API integration
- No browser automation needed

### Limitation
- API credits are limited on free tier
- Requires fallback for videos without captions

---

## Browser Automation Approach (Playwright)

**Date Added**: 2026-03-27

For cases where API methods fail, browser automation can extract transcripts by mimicking real user behavior.

**See detailed research**: [PLAYWRIGHT-BROWSER-AUTOMATION-RESEARCH.md](./PLAYWRIGHT-BROWSER-AUTOMATION-RESEARCH.md)

### Summary
- Official Playwright Docker images: `mcr.microsoft.com/playwright:v1.58.2-noble`
- Native SOCKS5 proxy support (Tor, WARP)
- Server mode: `npx playwright run-server --port 3000`
- Recommended: Kubernetes Job (on-demand) or KEDA scale-to-zero

### Quick Example
```python
from playwright.async_api import async_playwright

async def extract_transcript(video_id: str):
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            proxy={"server": "socks5://tor-proxy:9050"}
        )
        page = await browser.new_page()
        await page.goto(f"https://www.youtube.com/watch?v={video_id}")

        # Click "Show transcript" and extract
        await page.click('[aria-label*="transcript" i]')
        segments = await page.query_selector_all('ytd-transcript-segment-renderer')

        transcript = []
        for seg in segments:
            transcript.append(await seg.inner_text())

        await browser.close()
        return "\n".join(transcript)
```

---

## Updated Comparison Matrix (2026-03-27)

| Method | Cost | Reliability | Latency | Cloud IP Works? |
|--------|------|-------------|---------|-----------------|
| **Scrapingdog API** | Free (1,000) | High | Fast | Yes |
| **Supadata API** | Free (100) | High | Fast | Yes |
| **Playwright + Tor** | Free | Medium | Slow | Yes |
| **Playwright + Residential** | $50+/mo | High | Medium | Yes |
| **Audio + NVIDIA NIM** | Free (1,000) | High | Slow | Yes |
| **youtube-transcript-api + WARP** | Free | Low | Fast | Rate-limited |
| **yt-dlp subtitles** | Free | Very Low | Fast | Blocked (429) |

---

## References

### NVIDIA NIM
- [NVIDIA NIM Speech Models](https://build.nvidia.com/explore/speech)
- [Canary 1B v2 Model Card](https://build.nvidia.com/nvidia/canary-1b-v2)
- [NVIDIA Speech AI Blog](https://developer.nvidia.com/blog/nvidia-speech-ai-models-deliver-industry-leading-accuracy-and-performance/)
- [Free Access for Developers](https://developer.nvidia.com/blog/access-to-nvidia-nim-now-available-free-to-developer-program-members/)

### YouTube IP Blocking
- [yt-dlp PO Token Guide](https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide)
- [bgutil-ytdlp-pot-provider](https://github.com/Brainicism/bgutil-ytdlp-pot-provider)
- [bgutil-pot Docker Image](https://hub.docker.com/r/jim60105/bgutil-pot)
- [youtube-transcript-api](https://github.com/jdepoix/youtube-transcript-api)

### Proxy Solutions
- [Webshare Residential Proxies](https://www.webshare.io/)
- [Fixing RequestBlocked Error Guide](https://medium.com/@lhc1990/fixing-youtube-transcript-api-requestblocked-error-a-developers-guide-83c77c061e7b)

### Third-Party Transcript APIs
- [Scrapingdog YouTube Transcripts](https://www.scrapingdog.com/youtube-transcripts-api/)
- [Supadata YouTube Transcript API](https://supadata.ai/youtube-transcript-api)
- [TranscriptAPI.com](https://transcriptapi.com/)

### Browser Automation
- [Playwright Docker Documentation](https://playwright.dev/docs/docker)
- [Microsoft Container Registry - Playwright](https://mcr.microsoft.com/en-us/product/playwright/about)
- [Playwright Network/Proxy](https://playwright.dev/python/docs/network)
- [icoretech/browserless Helm Chart](https://artifacthub.io/packages/helm/icoretech/browserless)
