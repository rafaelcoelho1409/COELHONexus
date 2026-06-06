"""Row-3 mode picker for the Source stage — Search | Videos | Playlist
| Channel.

Same DOM contract as the old in-body `_ModeTabs` (each pill carries
`data-mode`; source.js binds by `[data-mode]`). Pills live in the
shell's row 3 (`StageToolbar` → `dd-toolbar-left`) and reuse the
exact row-2 stage classes (`.dd-substage-nav` / `.dd-substage`) so
they render identically to Source | Ingest | Ask. The class
prefix is naming history, not a feature gate."""
from __future__ import annotations

from fasthtml.common import Button, Nav


_MODES: list[tuple[str, str]] = [
    ("search",   "Search"),
    ("videos",   "Videos"),
    ("playlist", "Playlist"),
    ("channel",  "Channel"),
]


def SourceModeTabs(active: str = "search"):
    pills = [
        Button(
            label,
            type = "button",
            cls = "dd-substage active" if key == active else "dd-substage",
            data_mode = key,
        )
        for key, label in _MODES
    ]
    return Nav(*pills, cls = "dd-substage-nav",
               role = "tablist",
               aria_label = "Source mode")
