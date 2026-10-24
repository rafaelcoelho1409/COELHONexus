"""ycs/extract — yt-dlp metadata extraction tunables.py:L74-82`) + the aggregate-timeout clamp for playlist/channel
extraction (`helpers.py:L349,L398`)."""
from __future__ import annotations


# Process-level concurrency cap. Deprecated default = 10 (`helpers.py:L76`).
MAX_CONCURRENT_VIDEOS = 10

# Wall-clock per single-video extraction (full --dump-json).
# Deprecated default = 60s (`helpers.py:L77`).
TIMEOUT_PER_VIDEO_S = 60.0

# Channel / playlist extraction can be MUCH longer. Dynamic budget:
#   timeout = clamp(max_results * SECONDS_PER_VIDEO, MIN, MAX)
SECONDS_PER_VIDEO = 10
MIN_AGGREGATE_TIMEOUT_S = 120
MAX_AGGREGATE_TIMEOUT_S = 1800

# stdout buffer for asyncio.create_subprocess_exec — playlists can emit
# 30+ MB JSON for hundreds of videos. Deprecated default = 32 MB
# (`helpers.py:L78`); kept identical here.
BUFFER_LIMIT_BYTES = 32 * 1024 * 1024
