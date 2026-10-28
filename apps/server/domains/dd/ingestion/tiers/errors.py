from __future__ import annotations


class ManifestDetected(Exception):
    """Body is a link-index manifest, not content. Dispatcher falls through to the next declared tier (Tier 2 handles URL/Markdown pointers natively)."""


class EmptyLinksDetected(Exception):
    """llms.txt fetched OK but yielded zero per-page links; some sites use bare-URL bullets not [title](url) format. Dispatcher falls through to next tier."""


class TierUnavailable(Exception):
    """This tier's source is unusable right now — 404, a server error that outlasted the retries, an empty body, or an HTML page where a bundle was expected. The catalog may declare lower tiers for the same framework, so the dispatcher falls through to the next one instead of failing the run."""


# Every error that means "this source is unusable, try the next declared one" — the dispatcher catches exactly these; any other ends the run.
FALL_THROUGH: tuple[type[Exception], ...] = (ManifestDetected, EmptyLinksDetected, TierUnavailable)
