"""outline_sdp — pre-compiled regex (section-id format, heading markup)."""
from __future__ import annotations

import re


SECTION_ID_RE = re.compile(r"^s\d{1,3}$")   # s1, s2, ..., s999

# Markdown link inside a heading — `[text](url "title")`; group 1 is the link
# text (empty for permalink anchors like `[](#id "Link to this heading")`).
HEADING_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
# Permalink/icon glyphs that can survive as bare text: `¶` and Private-Use-Area
# icon-font chars (Sphinx RTD headerlink = U+F0C1).
HEADING_ICON_GLYPH_RE = re.compile(r"[¶\ue000-\uf8ff]")
# Backslash-escaped markdown punctuation (`open\_point\_in\_time`).
HEADING_MD_ESCAPE_RE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!])")
# ATX heading line, levels 1-3 (source headings the fallback outline mines).
ATX_HEADING_RE = re.compile(r"^#{1,3}[ \t]+(.+?)[ \t]*$")
# Code-fence delimiter line (``` or ~~~).
FENCE_LINE_RE = re.compile(r"^[ \t]*(?:```|~~~)")
