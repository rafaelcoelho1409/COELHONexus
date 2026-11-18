from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Page:
    """One page a transform makes of a capture — what every arm returns, and what the manifest and the store are written from.
    Order in the list is the order of the manifest."""
    slug:        str
    title:       str            # as the explorer shows it (the site's suffix off, repeated titles told apart)
    body:        str
    section:     str = ""       # the docs section the site files it under, "" when it has none
    source_url:  str = ""       # the page's own URL inside a bundle (`Source:` line), "" for a page that is the whole source
    tier:        str = ""       # "" = the tier of the capture; a changelog release carries its own (`post.params.CHANGELOG_TIER`)
