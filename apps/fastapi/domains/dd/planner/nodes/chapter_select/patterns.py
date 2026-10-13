"""chapter_select — pre-compiled regex."""
from __future__ import annotations

import re



# A page slug/title that says the page is a migration / upgrade / changelog page. Same vocabulary as
# order_chapters.patterns.TRAILING_RE (which pins such CHAPTERS last); the separators of a slug
# (-, _, /) are turned into spaces before matching.
MIGRATION_PAGE_RE = re.compile(
    r"\b(migrat\w*|upgrad\w*|changelog|release notes|deprecat\w*|breaking changes|what s new|whats new)\b",
    re.IGNORECASE,
)
# A chapter title that names orientation / support material (quickstart, foundations, troubleshooting, FAQ ...).
FRONT_MATTER_TITLE_RE = re.compile(
    r"\b(foundations?|quick ?start|getting started|introduction|overview|basics|troubleshoot\w*|faq|diagnostics|setup|installation)\b",
    re.IGNORECASE,
)
# `ingestion/<slug>/pages/0336-<url-slug>.md` → `<url-slug>`
KEY_STEM_RE = re.compile(r"(?:^|/)(?:\d+-)?([^/]+?)(?:\.md)?$")
