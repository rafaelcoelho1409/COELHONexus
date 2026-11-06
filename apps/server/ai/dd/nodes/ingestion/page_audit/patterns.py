from __future__ import annotations

import re


# Evidence that opens with what a Mintlify/MDX export leaves in a page: a JSX component (`<Note>`, `<CardGroup>`), an HTML
# media tag (`<img alt="" />`, `<iframe …/>`), or a code fence whose opener carries an attribute (```` ```python theme={null} ````).
# The corpus normalizer downstream already turns all of these into plain Markdown, so a finding that quotes one reports the
# export's convention, not a defect of the page.
EXPORT_CONVENTION_RE = re.compile(
    r"\A\s*(?:<[A-Z][A-Za-z]*\b|<(?:img|iframe|video)\b|(?:`{3,}|~{3,})[^\n]*\btheme=)"
)
