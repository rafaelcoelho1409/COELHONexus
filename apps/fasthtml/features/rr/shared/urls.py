"""Research Radar stage URL table + builder.

`RRStageSubNav` reads `_STAGES` to render the row-2 tab strip.
`stage_url(stage)` centralizes the path so callers (server-rendered tabs
+ client-side auto-navigation in main.js) agree on the convention. Pipeline
is the default landing surface; Digest is reachable directly when a scan
has finished. The active `?scan=<uuid>` is threaded through both so an
in-flight scan's state survives a tab switch."""


_STAGES = [
    ("pipeline", "Pipeline", "/research-radar"),
    ("digest",   "Digest",   "/research-radar/digest"),
]


def stage_url(stage_key: str, scan_id: str | None = None) -> str:
    base = next(
        (href for key, _, href in _STAGES if key == stage_key),
        "/research-radar",
    )
    return f"{base}?scan={scan_id}" if scan_id else base
