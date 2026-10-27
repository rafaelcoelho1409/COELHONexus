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

# An MDX comment (`{/* … */}`): react.dev puts one after every heading to carry its anchor (`## Reference {/*reference*/}`).
# Never content — the whitespace before it goes too.
MDX_COMMENT_RE = re.compile(r"[ \t]*\{/\*.*?\*/\}")

FENCE_META_HINT_RE = re.compile(
    rf"\b(?:{'|'.join(params.FENCE_META_ATTRS)})(?:\s*[=]|\s|$)",
)


# Raw-corpus boundary markers in llms-full.txt-style concatenations.
BOUNDARY_RE = re.compile(
    r"^\s*---\s+\S+\.md\s+---\s*$",
    re.MULTILINE,
)

# YAML frontmatter at top of file, or TOML's (`+++ … +++`, Hugo/Zola sites: the HTMX docs FastHTML links to).
FRONTMATTER_RE = re.compile(
    r"\A---\s*\r?\n(?P<body>.*?)\r?\n---\s*\r?\n",
    re.DOTALL,
)
TOML_FRONTMATTER_RE = re.compile(
    r"\A\+\+\+\s*\r?\n(?P<body>.*?)\r?\n\+\+\+\s*\r?\n",
    re.DOTALL,
)

# A heading at the top level of a page: `# Title`.
H1_LINE_RE = re.compile(r"^#[ \t]+\S")

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
# Narrow on purpose: link TEXT must be empty, `¶`, or a single Private-Use-Area
# icon glyph — a real trailing prose link (`…see [the guide](/x)`) never
# matches, so clean pages are untouched.
# (Verified 2026-09-29: zero matches across 63KB of Mintlify Tier-1 samples;
# dozens per page on MkDocs Tier-3 corpora like k3d/FastAPI.)
# PUA glyph (U+E000–U+F8FF): Sphinx's Read-the-Docs theme renders its
# headerlink as an icon-font char (U+F0C1), which markdownify emits as
# `[<U+F0C1>](#id "Link to this heading")` — invisible in a terminal/browser, so
# it looked like the empty-text case but never matched `¶?` (confirmed
# 2026-10-03 on elasticsearch-py.readthedocs.io: every heading and autodoc
# `dt` carried it, and leaked into outline_sdp's heuristic-fallback headings).
PERMALINK_TRAILING_RE = re.compile(r"\s*\[[¶\ue000-\uf8ff]?\]\([^)]*\)\s*$")
PERMALINK_LEADING_RE = re.compile(r"^\s*\[[¶\ue000-\uf8ff]?\]\(#[^)]*\)\s*")

# Mintlify puts an empty permalink in front of a heading's text (`## [](#overview) Overview`: 815 headings in deepagents' 79
# pages), where PERMALINK_LEADING_RE, which wants it at the start of the line, never looked; and Docusaurus-style API docs end
# a line with a `[#](#anchor)` link to itself (Playwright's `**Returns**` lists).
HEADING_PERMALINK_RE = re.compile(r"^(#{1,6}[ \t]+)(?:\[[¶-]?\]\(#[^)]*\)[ \t]*)+")
PERMALINK_HASH_RE = re.compile(r"[ \t]*\[#\]\(#[^)\s]*\)[ \t]*$")

# The header line a docs builder prints above a code block in its markdown: the language, a copy icon and the word "Copy"
# (Grafana's twin pages, 882 of them in Tempo's 213: `Bash ![Copy code to clipboard](…/icon-copy-small-2.svg) Copy`). The
# fence below says the language; the rest is a button.
CODE_COPY_LINE_RE = re.compile(
    r"^[ \t]*[\w+#.\-]{0,20}[ \t]*!\[(?:Copy code to clipboard|Copy to clipboard)\]\([^)\n]*\)[ \t]*(?:Copy(?: code)?)?[ \t]*$"
    r"|^[ \t]*[\w+#.\-]{0,20}[ \t]*!\[Copy(?: code)?\]\([^)\n]*\)[ \t]*Copy(?: code)?[ \t]*$",
    re.IGNORECASE,
)

# A code annotation a docs builder leaves at the end of a line of code to say "highlight this" (Shiki's transformer
# notation, in Mintlify's markdown): `# [!code highlight]`, `// [!code ++]`.
CODE_ANNOTATION_RE = re.compile(r"[ \t]*(?:#|//|--|/\*)[ \t]*\[!code[ \t]+[^\]\n]*\][ \t]*(?:\*/)?[ \t]*$")

# A line's leading indentation and blockquote markers: a fenced block inside a callout opens with `> ```bash …`.
QUOTE_PREFIX_RE = re.compile(r"^[ \t]*(?:>[ \t]?)*")

# `<video>` / `<audio>` on a line of its own: the whole element when it is self-closing or closed on the same line,
# otherwise the opening line of a multi-line one. `rest` is whatever follows the tag on that line.
MEDIA_OPEN_RE = re.compile(r"^\s*<(?P<tag>video|audio)\b[^>]*?(?P<selfclose>/)?>(?P<rest>.*)$", re.IGNORECASE)
MEDIA_SRC_RE = re.compile(r"\bsrc\s*=", re.IGNORECASE)

# The licence header of a Hugging Face docs source: an HTML comment whose opening line the doc builder drops, leaving
# `#`-prefixed lines (which read as H1s: `# Licensed under the Apache License…`) up to the comment's closing `-->` — on a
# line of its own (Optimum) or at the end of the last `#` line (`# limitations under the License. -->`, most of Diffusers).
LICENSE_HEADER_RE = re.compile(r"\A\s*(?:#(?:(?!-->)[^\n])*\n)*(?:#[^\n]*?)?-->[ \t]*\n+")

# The anchor Hugging Face's doc builder appends to a heading (`## AutoModel[[transformers.AutoModel]]`): its own id for the
# heading, which nothing in the stored page links to. It is read as part of the heading's text — and of the page's title.
HEADING_ANCHOR_RE = re.compile(r"^(#{1,6}[ \t]+.*?)[ \t]*\[\[[A-Za-z0-9_.:\-]+\]\][ \t]*$")

# What the host tacks on at the foot of every page for agents: react.dev's `---` / `## Sitemap` / `[Overview of all docs
# pages](/llms.txt)`. Looked for in the last few hundred characters of a page only, and only when nothing but a link to
# the site's llms.txt follows the heading.
AGENT_FOOTER_RE = re.compile(
    r"(?:\n[ \t]*-{3,}[ \t]*)?\n+#{1,3}[ \t]+(?:Sitemap|Documentation Index)[ \t]*\n+"
    r"(?:[-*][ \t]+)?\[[^\]\n]*\]\([^)\s]*llms(?:-full)?\.txt\)[ \t]*\n*\Z",
    re.IGNORECASE,
)

# The horizontal rules react.dev leaves between its last section and the footer: more than one when the last section's own
# rule is there too.
TRAILING_RULES_RE = re.compile(r"(?:\s*\n[ \t]*-{3,}[ \t]*)+\Z")

# A JSX `<img>` (`<img className="w-full" src="…" alt="…" />`): not Markdown, so a reader that does not render raw HTML
# shows the tag as text. Rewritten as `![alt](src)`; the `dark-image` half of a light/dark pair is the same picture again.
IMG_TAG_RE = re.compile(r"<img\b(?P<attrs>[^<>]*?)/?>", re.IGNORECASE)
IMG_SRC_RE = re.compile(r"\bsrc\s*=\s*(?:\"([^\"]*)\"|'([^']*)')", re.IGNORECASE)
IMG_ALT_RE = re.compile(r"\balt\s*=\s*(?:\"([^\"]*)\"|'([^']*)')", re.IGNORECASE)
IMG_DARK_RE = re.compile(r"\bclass(?:Name)?\s*=\s*[\"'][^\"']*\bdark(?:-image|-only)\b", re.IGNORECASE)

# A line of a block quote (up to three spaces of indentation, as CommonMark allows).
QUOTE_LINE_RE = re.compile(r"^[ \t]{0,3}>")

# What marks a block quote at the very top of a page as the host's notice to agents rather than part of the docs: a link to
# the site's own llms.txt ("> Use this file for an index of the main pages: https://qdrant.tech/llms.txt"), or the
# "Documentation Index" heading Mintlify puts there.
AGENT_BANNER_RE = re.compile(r"https?://\S*llms(?:-full)?\.txt|documentation index", re.IGNORECASE)

# Zero-width + BOM + miscellaneous formatting chars.
ZERO_WIDTH_RE = re.compile(r"[​‌‍⁠﻿]")

# Opener of a fenced block that sits behind ≥1 space/tab of indentation —
# markdownify nests `<pre>` inside Sphinx `<dd>` bodies as `:   ` definition-list
# content, i.e. 4 spaces of indent, which CommonMark reads as an indented code
# block / paragraph continuation instead of a fence (see `_hoist_nested_fences_pass`).
INDENTED_FENCE_OPEN_RE = re.compile(r"^(?P<indent>[ \t]+)(?P<fence>`{3,}|~{3,})(?P<info>.*)$")
