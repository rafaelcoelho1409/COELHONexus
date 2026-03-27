# IP Rotation & Anonymity Tools Research

> Deep research on FREE self-hosted IP rotation tools for COELHOCloud, covering cybersecurity, pentesting, and OSINT use cases.

## Context

YouTube transcript extraction from COELHOCloud was blocked due to IP restrictions. Cloud IPs are frequently flagged by services like YouTube, requiring IP rotation or proxy infrastructure to bypass these blocks.

This research identifies FREE, self-hostable alternatives to paid proxy services (Bright Data, Oxylabs, residential proxies) and paid VPNs (Surfshark, NordVPN).

---

## Verified Docker Images (Final Selection)

After extensive research comparing multiple options, these are the **verified best choices**:

### WARP - `caomingjun/warp`

| Metric | Value |
|--------|-------|
| **GitHub Repository** | [cmj2002/warp-docker](https://github.com/cmj2002/warp-docker) |
| **GitHub Stars** | 855 |
| **Uses Official Client** | Yes (official Cloudflare WARP client, not wgcf) |
| **Port** | 1080 (SOCKS5 + HTTP) |
| **Documentation** | Excellent (env vars, docker-compose, troubleshooting) |
| **Last Updated** | 2025 (actively maintained) |

**Why this image wins:**
- Uses **official Cloudflare WARP client** (wgcf is blocked in some regions)
- Includes GOST for SOCKS5/HTTP proxy support
- Well-documented with docker-compose examples
- Active maintenance with troubleshooting guides

### Tor - `peterdavehello/tor-socks-proxy`

| Metric | Value |
|--------|-------|
| **GitHub Repository** | [PeterDaveHello/tor-socks-proxy](https://github.com/PeterDaveHello/tor-socks-proxy) |
| **GitHub Stars** | 622 |
| **Image Size** | 10MB (smallest available) |
| **Port** | 9150 (SOCKS5) |
| **Security** | Runs as `tor` user (non-root) |
| **Documentation** | Good |

**Why this image wins:**
- **Smallest image** (10MB vs 50MB+ for alternatives)
- **Most secure** - runs as non-root `tor` user
- Automatic IP rotation every 10 minutes
- No security concerns like `dperson/torproxy` (may require root)

### Rejected Alternatives

| Image | Reason for Rejection |
|-------|---------------------|
| `monius/docker-warp-socks` | Uses wgcf (blocked in some regions by Cloudflare) |
| `dperson/torproxy` | May require `TORUSER=root` (security risk), 50MB+ |
| `jfwenisch/alpine-tor` | Less stars, less documented |
| `patrickplaggenborg/warp-proxy` | 0 stars, not maintained |

---

## YouTube Effectiveness Analysis

### Official youtube-transcript-api Findings

From [GitHub Discussion #335](https://github.com/jdepoix/youtube-transcript-api/discussions/335):

| Solution | Works for YouTube? | Notes |
|----------|-------------------|-------|
| **Webshare Residential** | Confirmed | **PAID** ($6/month) - Official recommendation |
| **WARP** | Likely | Confirmed for yt-dlp, not officially tested for transcript API |
| **Tor** | Unlikely | Exit nodes publicly known, actively blocked by YouTube |

### Key Insight

From [blog.arfevrier.fr](https://blog.arfevrier.fr/leveraging-cloudflare-warp-to-bypass-youtubes-api-restrictions/):
> "YouTube considers Cloudflare IPs not to be blacklisted"

This confirms WARP is the **best FREE option** for YouTube access.

---

## Tool Categories

### Tier 1 - Essential (Recommended for COELHOCloud)

| Tool | Docker Image | Port | Purpose |
|------|--------------|------|---------|
| **WARP** | `caomingjun/warp` | 1080 | Cloudflare's free VPN with residential-like IPs |
| **Tor** | `peterdavehello/tor-socks-proxy` | 9150 | IP rotation via circuit changes |

### Tier 2 - Enhanced Security (Future)

| Tool | Type | Purpose |
|------|------|---------|
| **Proxychains-ng** | Proxy Wrapper | Force any TCP application through proxy chain |
| **Shadowsocks** | Encrypted Tunnel | High-speed SOCKS5 proxy with encryption |
| **HAProxy** | Load Balancer | Distribute requests across multiple Tor instances |

### Tier 3 - OSINT & Pentesting (Future)

| Tool | Category | Purpose |
|------|----------|---------|
| **SpiderFoot** | OSINT | Automated reconnaissance, 200+ data sources |
| **theHarvester** | OSINT | Email, subdomain, IP enumeration |
| **Recon-ng** | OSINT | Modular reconnaissance framework |
| **Amass** | OSINT | DNS enumeration and network mapping |

---

## Docker Deployment

### WARP (caomingjun/warp)

```yaml
services:
  warp:
    image: caomingjun/warp
    container_name: warp
    restart: always
    ports:
      - "1080:1080"
    environment:
      - WARP_SLEEP=2
    cap_add:
      - MKNOD
      - AUDIT_WRITE
      - NET_ADMIN
    sysctls:
      - net.ipv6.conf.all.disable_ipv6=0
      - net.ipv4.conf.all.src_valid_mark=1
    volumes:
      - ./warp-data:/var/lib/cloudflare-warp
    device_cgroup_rules:
      - 'c 10:200 rwm'
```

**Environment Variables:**

| Variable | Purpose | Default |
|----------|---------|---------|
| `WARP_SLEEP` | Daemon startup delay (seconds) | 2 |
| `WARP_LICENSE_KEY` | WARP+ subscription key | (optional) |
| `GOST_ARGS` | Proxy configuration | `-L :1080` |

**Verify connectivity:**
```bash
curl --socks5-hostname 127.0.0.1:1080 https://cloudflare.com/cdn-cgi/trace
# Should show: warp=on
```

### Tor (peterdavehello/tor-socks-proxy)

```yaml
services:
  tor:
    image: peterdavehello/tor-socks-proxy:latest
    container_name: tor
    restart: always
    ports:
      - "9150:9150"
```

**Verify connectivity:**
```bash
curl --socks5-hostname 127.0.0.1:9150 https://check.torproject.org/api/ip
# Should show: {"IsTor":true}
```

---

## Comparison: Free Tools vs Paid VPNs

| Feature | WARP + Tor | Paid VPN (Surfshark) |
|---------|------------|----------------------|
| Cost | Free | ~$3/month |
| IP Rotation | Yes (Tor every 10min) | No |
| Country Selection | Limited | 100+ countries |
| Streaming Geo-unblock | No | Yes |
| Speed | WARP: Fast, Tor: Slow | Fast |
| Anonymity | Tor: Excellent | Trust-based |
| Self-hosted | Yes | No |
| Mobile Apps | No | Yes |
| Technical Knowledge | Required | Not needed |
| OSINT/Pentesting | Excellent | Poor |

### Verdict

**Free tools replace paid VPNs for:**
- IP rotation for scraping/automation
- True anonymity (Tor > any VPN)
- Security research and pentesting
- Privacy from ISP (WARP)
- Self-controlled infrastructure

**Free tools cannot replace paid VPNs for:**
- Streaming geo-unblock (Netflix, etc.)
- Easy country selection
- Non-technical users
- Mobile convenience

---

## Architecture for COELHOCloud

```
+---------------------------------------------------------------------+
|                          COELHOCloud                                |
|                                                                     |
|  +---------------------------------------------------------------+  |
|  |                       Proxy Stack                             |  |
|  |                                                               |  |
|  |   +-------------------+      +-------------------+            |  |
|  |   |  caomingjun/warp  |      | peterdavehello/   |            |  |
|  |   |  (Official WARP)  |      | tor-socks-proxy   |            |  |
|  |   |                   |      |                   |            |  |
|  |   |  Port: 1080       |      |  Port: 9150       |            |  |
|  |   |  SOCKS5 + HTTP    |      |  SOCKS5 only      |            |  |
|  |   +--------+----------+      +--------+----------+            |  |
|  |            |                          |                       |  |
|  |   warp-proxy.ts.net:1080     tor-proxy.ts.net:9150            |  |
|  |            |                          |                       |  |
|  |            +------------+-------------+                       |  |
|  |                         |                                     |  |
|  |                         v                                     |  |
|  |              +---------------------+                          |  |
|  |              |     FastAPI Pod     |                          |  |
|  |              |                     |                          |  |
|  |              |  1. Try WARP first  |                          |  |
|  |              |  2. Fallback to Tor |                          |  |
|  |              |  3. Return error    |                          |  |
|  |              +---------------------+                          |  |
|  +---------------------------------------------------------------+  |
|                                                                     |
|  Existing Services:                                                 |
|  - redis-tcp.YOUR_TAILNET_DOMAIN.ts.net:6379                                |
|  - elasticsearch.YOUR_TAILNET_DOMAIN.ts.net                                  |
|  - neo4j.YOUR_TAILNET_DOMAIN.ts.net:7687                                    |
|                                                                     |
+---------------------------------------------------------------------+
```

---

## Implementation Priority for COELHO Nexus

### Phase 1 - Unblock YouTube (Immediate)

1. Deploy WARP container on COELHOCloud via Terraform
2. Expose via Tailscale as `warp-proxy.YOUR_TAILNET_DOMAIN.ts.net:1080`
3. Update FastAPI to use WARP for youtube-transcript-api

### Phase 2 - Add Fallback

1. Deploy Tor container on COELHOCloud via Terraform
2. Expose via Tailscale as `tor-proxy.YOUR_TAILNET_DOMAIN.ts.net:9150`
3. Update FastAPI to fallback to Tor if WARP fails

### Phase 3 - Scale (If Needed)

1. Add multiple Tor instances
2. Deploy HAProxy for load balancing
3. Implement IP rotation via control port

---

## Usage in FastAPI

```python
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api.proxies import GenericProxyConfig

# WARP proxy (primary) - uses official Cloudflare client
warp_proxy = GenericProxyConfig(
    http_url="socks5://warp-proxy.YOUR_TAILNET_DOMAIN.ts.net:1080",
    https_url="socks5://warp-proxy.YOUR_TAILNET_DOMAIN.ts.net:1080",
)

# Tor proxy (fallback) - 10MB lightweight image
tor_proxy = GenericProxyConfig(
    http_url="socks5://tor-proxy.YOUR_TAILNET_DOMAIN.ts.net:9150",
    https_url="socks5://tor-proxy.YOUR_TAILNET_DOMAIN.ts.net:9150",
)

def get_transcript_with_fallback(video_id: str):
    # Try WARP first (faster, Cloudflare IPs not blacklisted)
    try:
        return YouTubeTranscriptApi.get_transcript(
            video_id,
            proxy_config=warp_proxy
        )
    except Exception:
        pass

    # Fallback to Tor (slower, but free IP rotation)
    return YouTubeTranscriptApi.get_transcript(
        video_id,
        proxy_config=tor_proxy
    )
```

---

## References

- [cmj2002/warp-docker](https://github.com/cmj2002/warp-docker) - 855 stars, official WARP client
- [PeterDaveHello/tor-socks-proxy](https://github.com/PeterDaveHello/tor-socks-proxy) - 622 stars, 10MB
- [youtube-transcript-api Discussion #335](https://github.com/jdepoix/youtube-transcript-api/discussions/335)
- [Leveraging Cloudflare WARP for YouTube](https://blog.arfevrier.fr/leveraging-cloudflare-warp-to-bypass-youtubes-api-restrictions/)
- [Tor Project](https://www.torproject.org/)

---

*Document updated: 2026-03-26*
*Research conducted for COELHO Nexus YouTube transcript extraction issue*
