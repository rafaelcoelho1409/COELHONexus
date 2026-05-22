"""Shared exception types for tier dispatch control-flow."""


class ManifestDetected(Exception):
    """Body looks like a manifest (link index) not real content — caller
    should fall through to Tier 2 which natively consumes those URL/Markdown
    pointers."""


class EmptyLinksDetected(Exception):
    """Raised when llms.txt was fetched successfully but parsed zero
    usable per-page links. Some sites publish a long-form prose llms.txt
    (per the llmstxt.org spec) with bare-URL bullets like
    `- GitHub: https://github.com/…` instead of the `- [title](url)`
    markdown-link format our parser expects. In that case the dispatcher
    should fall through to the next available tier (sitemap/docs/github)
    rather than fail the run — Tier 2 simply isn't usable here."""
    pass
