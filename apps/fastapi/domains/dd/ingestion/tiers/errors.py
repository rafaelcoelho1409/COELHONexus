from __future__ import annotations


class ManifestDetected(Exception):
    """Body is a link-index manifest, not content. Dispatcher falls through to Tier 2 which handles URL/Markdown pointers natively."""


class EmptyLinksDetected(Exception):
    """llms.txt fetched OK but yielded zero per-page links; some sites use bare-URL bullets not [title](url) format. Dispatcher falls through to next tier."""
