from __future__ import annotations


class ManifestDetected(Exception):
    """Body looks like a manifest (link index), not real content. Caller falls
    through to Tier 2 which natively consumes URL/Markdown pointers."""


class EmptyLinksDetected(Exception):
    """llms.txt fetched successfully but parsed zero usable per-page links —
    some sites publish prose llms.txt with bare-URL bullets (`- GitHub: https://...`)
    instead of the `- [title](url)` markdown-link format. Dispatcher falls through
    to the next tier rather than failing the run."""
