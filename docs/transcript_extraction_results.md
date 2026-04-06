# YouTube Transcript Extraction Test Results

## Test Configuration
- **Date:** 2026-04-05
- **Videos tested:** 25 (from "capital global" search)
- **Viewport:** 1920x1080

## Results Comparison

| Test | Concurrency | Blockers | Transcriptions | Success Rate | Notes |
|------|-------------|----------|----------------|--------------|-------|
| Baseline (original commit) | 5 | None | 15/25 | 60% | No optimizations |
| With broken optimizations | 5 | All + #secondary removal | 3/25 | 12% | Removed transcript panel! |
| With fixed optimizations | 5 | Video/ads/analytics/images | 14/25 | 56% | Keep #secondary intact |

## Current Blockers (BLOCK_PATTERNS)

### Video/Audio Streaming
- `**/videoplayback*`
- `**/googlevideo.com/*`
- `**/*.googlevideo.com/*`
- `**/*.m3u8` (HLS manifests)
- `**/*.ts` (HLS segments)
- `**/manifest*` (DASH manifests)

### Ads
- `**/doubleclick.net/*`
- `**/googleadservices.com/*`
- `**/googlesyndication.com/*`
- `**/googleads.g.doubleclick.net/*`
- `**/youtube.com/pagead/*`
- `**/adservice.google.com/*`
- `**/ads?*`
- `**/pagead*`

### Analytics/Tracking
- `**/google-analytics.com/*`
- `**/googletagmanager.com/*`
- `**/youtube.com/api/stats/*`
- `**/youtube.com/ptracking*`
- `**/s.youtube.com/*`
- `**/youtubei/v1/log*`
- `**/log_interaction*`

### Images
- `**/*.jpg`, `**/*.jpeg`, `**/*.png`, `**/*.gif`, `**/*.webp`
- `**/yt3.ggpht.com/*`
- `**/i.ytimg.com/*`

### Resource Types Blocked
- `media`, `image`, `font`

## CSS Hiding (HIDE_VIDEO_CSS)
```css
video, #movie_player, .html5-video-player, .video-stream,
#player-container-inner, .ytp-cued-thumbnail-overlay {
    display: none !important;
    visibility: hidden !important;
}
```

## DOM Cleanup (CLEANUP_DOM_JS)
- Stop and remove video element
- Remove `#movie_player` container
- Remove `#comments` section
- Kill all timers (setTimeout/setInterval)

## Failure Analysis (5 concurrent, 14/25 success)

### "Transcript button not found" (7 videos)
These videos likely don't have transcripts available on YouTube:
- StrYEFm938g, FeAezGTly04, mDb1RMXFDBU, aCM8qGvv7uM
- wSIHCDBM_zw, 4rBUAHqKuDw, 6PILNrL3AXk

### "Transcript panel not loaded" (4 videos)
Transcripts exist but panel didn't load in time:
- jrba59bUgNA, 8CU0a0VFMRQ, 2ewl8SVLh9c, ykXM6kNdBJA

## Sequential Extraction Test (max_concurrent=1)

| Test | Concurrency | Videos | Transcriptions | Success Rate | Avg Time |
|------|-------------|--------|----------------|--------------|----------|
| Sequential | 1 | 15 | 0/15 | 0% | ~270s/video timeout |

**Result:** Sequential made it WORSE. With pool_size=1, "Pool exhausted" occurs constantly, causing context creation issues. Each video timed out after ~270 seconds.

**Conclusion:** Keep `max_concurrent=5` which achieved 14/25 (56%) success rate.
