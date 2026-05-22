"""corpus_normalize — module-level constants."""
from __future__ import annotations

import re


_NORMALIZER_VERSION = 1   # bump on any pass change; invalidates cache


# MDX wrapper tag set (Mintlify v4, Docusaurus 3.x, Nextra 4, Starlight,
# ReadMe.io, GitBook html). Tags are STRIPPED but inner text is preserved.
# Keep this list lowercase since regex matches are case-sensitive — these
# are JSX/MDX components which conventionally start uppercase, plus a few
# HTML-ish lowercase forms.
_MDX_WRAPPER_TAGS = (
    # Mintlify v4
    "Tip", "Note", "Warning", "Info", "Caution", "Check", "Danger",
    "Tabs", "TabItem", "Tab", "Accordion", "AccordionGroup",
    "CodeGroup", "CodeBlock",
    "Steps", "Step", "Card", "CardGroup", "Frame",
    "ParamField", "ResponseField", "Expandable",
    "Callout", "Hint",
    # Starlight
    "Aside", "LinkCard", "FileTree",
    # Nextra 4
    "Tree",
)

# Open tag: <Tag attr="..." attr> or <Tag /> ; Close tag: </Tag>
# Whitespace-tolerant. Inner-text preserving (we match only the tag
# markup, not its body).
_MDX_TAGS_PATTERN = "|".join(re.escape(t) for t in _MDX_WRAPPER_TAGS)
_MDX_OPEN_TAG_RE  = re.compile(
    rf"<(?:{_MDX_TAGS_PATTERN})(?:\s+[^>]*?)?/?>",
)
_MDX_CLOSE_TAG_RE = re.compile(
    rf"</(?:{_MDX_TAGS_PATTERN})\s*>",
)

# Mintlify code-fence attribute names per docs.mintlify.com/code (May 2026).
# Used to detect "is this info-string Mintlify-styled?" — we always
# reduce info-string to first whitespace-separated token (the lang),
# so this list is only for stat tracking + identifying the
# "metadata seen" case for the report.
_FENCE_META_ATTRS = (
    "theme", "expandable", "lines", "title", "icon",
    "wrap", "highlight", "focus", "filename", "copy",
    "twoslash", "lineNumbers", "actions",
)
_FENCE_META_HINT_RE = re.compile(
    rf"\b(?:{'|'.join(_FENCE_META_ATTRS)})(?:\s*[=]|\s|$)",
)

# Raw-corpus boundary markers in llms-full.txt-style concatenations.
# Match `--- something.md ---` on its OWN line (whitespace OK around).
# Use `--- <slug>.md ---` shape conservatively to avoid matching
# legitimate horizontal-rule `---` which has only 3+ dashes alone.
_BOUNDARY_RE = re.compile(
    r"^\s*---\s+\S+\.md\s+---\s*$",
    re.MULTILINE,
)

# Frontmatter at top of file. YAML block delimited by `---` on its own
# line at start, then `---` again on its own line ending the block.
_FRONTMATTER_RE = re.compile(
    r"\A---\s*\r?\n(?P<body>.*?)\r?\n---\s*\r?\n",
    re.DOTALL,
)

# Container admonitions (Docusaurus / VitePress / MkDocs Material
# subset). Strip the OPEN line + the matching CLOSE line; inner text
# survives. Conservative: only strip lines that look UNAMBIGUOUSLY
# like admonition delimiters (no false positives on `:::` mid-prose).
_ADMON_KINDS = (
    "tip", "note", "warning", "caution", "info", "danger", "important",
    "details", "abstract",
)
_ADMON_OPEN_RE = re.compile(
    rf"^\s*:::\s*(?:{'|'.join(_ADMON_KINDS)})(?:\s+.*)?\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_ADMON_CLOSE_RE = re.compile(
    r"^\s*:::\s*$",
    re.MULTILINE,
)
# GitBook hint blocks
_GITBOOK_HINT_OPEN_RE = re.compile(
    r"^\s*\{%\s*hint\s+[^%]*%\}\s*$",
    re.MULTILINE,
)
_GITBOOK_HINT_CLOSE_RE = re.compile(
    r"^\s*\{%\s*endhint\s*%\}\s*$",
    re.MULTILINE,
)
# GitBook tabs
_GITBOOK_TABS_OPEN_RE = re.compile(
    r"^\s*\{%\s*tabs?\s*%\}\s*$",
    re.MULTILINE,
)
_GITBOOK_TABS_CLOSE_RE = re.compile(
    r"^\s*\{%\s*endtabs?\s*%\}\s*$",
    re.MULTILINE,
)

# Zero-width + BOM + miscellaneous formatting chars.
_ZERO_WIDTH_RE = re.compile(
    r"[​‌‍⁠﻿]",
)

# HTML entities we always want decoded back to their unicode form.
# Restricted list — we DON'T blanket-decode (e.g. `&copy;` is fine as-is).
_ENTITY_DECODES = (
    ("&amp;",  "&"),
    ("&lt;",   "<"),
    ("&gt;",   ">"),
    ("&quot;", '"'),
    ("&#39;",  "'"),
    ("&apos;", "'"),
    ("&nbsp;", " "),
)
