"""What the A/B measures with. Deliberately NOT the normalizer's own patterns (`synth.nodes.corpus_normalize.patterns`): an arm that
is scored with the regexes it was written against wins by construction. These look at the page the way a reader does."""
from __future__ import annotations

import re


# ---- noise: what a reader sees on a page that is not the page, found outside code fences ------------------------------------
NOISE = {
    # a line that is only a JSX/MDX component tag (`<Note>`, `</Tabs>`, `<Frame />`)
    "jsx_component_line": re.compile(r"^\s*</?[A-Z][A-Za-z0-9]*(\s[^>]*)?/?>\s*$"),
    # a line that is only an HTML layout tag
    "html_tag_line": re.compile(r"^\s*</?(div|span|p|br|img|video|audio|iframe|details|summary|table|center|a|picture|source)\b[^>]*>\s*$", re.IGNORECASE),
    # a heading anchor left as a link (`## [](#intro) Intro`, `[¶](#x)`, `[#](#x)`)
    "empty_permalink": re.compile(r"\[\]\(#[^)]*\)|\[[#¶]\]\(#[^)]*\)"),
    # a code fence opened with the site's own attributes (```bash theme={...})
    "fence_attr": re.compile(r"^\s*(>\s*)*(```|~~~)\w+\s+\S+=\{?"),
    "mdx_comment": re.compile(r"\{/\*.*?\*/\}"),
    # the page's own furniture
    "page_furniture": re.compile(r"^\s*(Copy|Copy page|Copy code|Was this page helpful\??|Edit this page|On this page|Back to top)\s*$", re.IGNORECASE),
    # a container marker whose inner text was kept (`:::note`, `{% hint %}`)
    "admonition_marker": re.compile(r"^\s*(:::\w*|\{%\s*(hint|tabs|endhint|endtabs|tab|endtab)[^%]*%\})\s*$"),
    # the host's note to agents, left in the page
    "agent_banner": re.compile(r"llms(-full)?\.txt", re.IGNORECASE),
    # a link that opens a page of this app, not the docs: site-relative or `./`. Not an image: the server downloads and relinks those
    # after every arm alike (the shared artifact stage), so a page that still names `images/x.png` is as it should be at this point.
    "relative_link": re.compile(r"(?<!!)\[[^\]]*\]\((?!https?://|#|mailto:|/api/v1/)[^)\s]*\)"),
}

# ---- what a page says ----------------------------------------------------------------------------------------------------
# Words of the prose (the destinations of links are dropped first: making a link absolute must not read as invented text).
WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)
LINK_DESTINATION_RE = re.compile(r"\]\([^)\s]*(?:\s+\"[^\"]*\")?\)")
BARE_URL_RE = re.compile(r"https?://\S+")

# A line of code a site annotated for its highlighter (`x = 1  # [!code highlight]`): the normalizer takes the annotation off, which
# is a declared change, not a lost one.
CODE_ANNOTATION_RE = re.compile(r"\s*(?:#|//|/\*|<!--)?\s*\[!code [^\]]*\]\s*(?:\*/|-->)?\s*$")

# A title compared across two spellings of it (`domain.title_key`).
NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
MD_LINK_TEXT_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
INLINE_MARKS_RE = re.compile(r"[`*_]")
