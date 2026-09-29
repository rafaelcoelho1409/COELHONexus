"""corpus_normalize — pre-compiled regex (MDX tags, boundaries,
frontmatter, admonitions, GitBook hint/tabs, zero-width)."""
from __future__ import annotations
from . import params

import re



_MDX_TAGS_PATTERN = "|".join(re.escape(t) for t in params.MDX_WRAPPER_TAGS)

# Whitespace-tolerant. Inner-text preserving (we match only the tag
# markup, not its body).
MDX_OPEN_TAG_RE  = re.compile(
    rf"<(?:{_MDX_TAGS_PATTERN})(?:\s+[^>]*?)?/?>",
)
MDX_CLOSE_TAG_RE = re.compile(
    rf"</(?:{_MDX_TAGS_PATTERN})\s*>",
)

FENCE_META_HINT_RE = re.compile(
    rf"\b(?:{'|'.join(params.FENCE_META_ATTRS)})(?:\s*[=]|\s|$)",
)


# Raw-corpus boundary markers in llms-full.txt-style concatenations.
BOUNDARY_RE = re.compile(
    r"^\s*---\s+\S+\.md\s+---\s*$",
    re.MULTILINE,
)

# YAML frontmatter at top of file.
FRONTMATTER_RE = re.compile(
    r"\A---\s*\r?\n(?P<body>.*?)\r?\n---\s*\r?\n",
    re.DOTALL,
)

# Container admonitions (Docusaurus / VitePress / MkDocs Material subset).
ADMON_OPEN_RE = re.compile(
    rf"^\s*:::\s*(?:{'|'.join(params.ADMON_KINDS)})(?:\s+.*)?\s*$",
    re.IGNORECASE | re.MULTILINE,
)
ADMON_CLOSE_RE = re.compile(
    r"^\s*:::\s*$",
    re.MULTILINE,
)

# GitBook hint blocks.
GITBOOK_HINT_OPEN_RE = re.compile(
    r"^\s*\{%\s*hint\s+[^%]*%\}\s*$",
    re.MULTILINE,
)
GITBOOK_HINT_CLOSE_RE = re.compile(
    r"^\s*\{%\s*endhint\s*%\}\s*$",
    re.MULTILINE,
)
# GitBook tabs.
GITBOOK_TABS_OPEN_RE = re.compile(
    r"^\s*\{%\s*tabs?\s*%\}\s*$",
    re.MULTILINE,
)
GITBOOK_TABS_CLOSE_RE = re.compile(
    r"^\s*\{%\s*endtabs?\s*%\}\s*$",
    re.MULTILINE,
)

# Static-site permalink debris (MkDocs Material `[¶](#slug "Permanent link")`
# appended to every heading; empty-text `[](#anchor)` prepended by others).
# Narrow on purpose: link TEXT must be empty or `¶` — a real trailing prose
# link (`…see [the guide](/x)`) never matches, so clean pages are untouched.
# (Verified 2026-09-29: zero matches across 63KB of Mintlify Tier-1 samples;
# dozens per page on MkDocs Tier-3 corpora like k3d/FastAPI.)
PERMALINK_TRAILING_RE = re.compile(r"\s*\[¶?\]\([^)]*\)\s*$")
PERMALINK_LEADING_RE = re.compile(r"^\s*\[¶?\]\(#[^)]*\)\s*")

# Zero-width + BOM + miscellaneous formatting chars.
ZERO_WIDTH_RE = re.compile(r"[​‌‍⁠﻿]")
