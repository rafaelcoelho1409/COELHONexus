"""ycs/content — yt-dlp subprocess tunables + base args.

These tune the subprocess search path used by the synchronous /search
endpoint. The PO Token sidecar must be reachable at the URL declared in
`_POT_PROVIDER_URL` for age-gated videos (Helm/Compose ships bgutil-PoT
as a sidecar on the same pod / same localhost).
"""
from __future__ import annotations


# Process-level concurrency cap on outbound yt-dlp invocations.
MAX_CONCURRENT = 10
# Wall-clock per invocation; the deprecated stack settled on 60s for
# metadata extraction and 90s for searches (search fans out across
# fetch_count entries, --flat-playlist).
TIMEOUT_S = 60.0
SEARCH_TIMEOUT_S = 90.0
# stdout buffer for `asyncio.create_subprocess_exec(limit=...)` — yt-dlp
# `--dump-single-json` on a 50-result search can emit ~5-10 MB. 32 MB
# leaves plenty of headroom for full-playlist extractions.
BUFFER_LIMIT_BYTES = 32 * 1024 * 1024
# When ANY post-fetch filter is set, we ask yt-dlp for 3× the desired
# result count so the match-filter pass still yields max_results after
# rejecting non-matchers. Tuned empirically on the deprecated stack.
FETCH_MULTIPLIER_FILTERED = 3


# Sidecar URL — bgutil-ytdlp-pot-provider speaks plain HTTP on this port.
# Keep in sync with the Helm `bgutil-pot` sidecar service port.
_POT_PROVIDER_URL = "http://127.0.0.1:4416"


# Args every yt-dlp invocation shares. Kept here (not as `keys.py`)
# because they are tunables for the SUBPROCESS interface, not storage
# key shapes.
BASE_ARGS: tuple[str, ...] = (
    "yt-dlp",
    "--no-download",
    "--no-warnings",
    "--ignore-errors",
    "--no-clean-info-json",
    "--force-ipv4",
    "--socket-timeout", "15",
    "--retries", "3",
    "--age-limit", "0",
    "--extractor-args", "youtube:skip=dash,hls,translated_subs",
    "--extractor-args", f"youtubepot-bgutilhttp:base_url={_POT_PROVIDER_URL}",
)
