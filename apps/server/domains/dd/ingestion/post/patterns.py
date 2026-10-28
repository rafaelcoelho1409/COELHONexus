from __future__ import annotations

import re


# Mintlify/Streamlit llms-full.txt boundary marker.
SOURCE_LINE_RE = re.compile(
    r'^Source:\s+(https?://\S+)\s*$', re.MULTILINE,
)


# A URL folder that is a release, not a topic: `v5.21.0`, `v0.42.1`, `0.42`, `v1`, `2.x`. (Docs hosts keep every page of a
# release under it — Hugging Face's `/docs/transformers/v5.21.0/…` — so it says nothing about where a page belongs.)
VERSION_SEGMENT_RE = re.compile(r"^v?\d+(?:\.(?:\d+|x))*(?:[-.]?(?:rc|alpha|beta|dev|post)\d*)?$", re.IGNORECASE)


# The horizontal rule(s) that end a section of a page: the line that separated it from the next one.
TRAILING_RULE_RE = re.compile(r"(?:\s*\n[ \t]*-{3,}[ \t]*)+\Z")


# A fenced code block's opening line (backticks or tildes), whatever it sits inside.
CODE_FENCE_RE = re.compile(r"^[ \t>]*(?:`{3,}|~{3,})", re.MULTILINE)

# A markdown link or image as running text: group 1 is its visible text.
MD_LINK_TEXT_RE = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")

# A fence line, once indentation and blockquote markers are off: group 1 is the run of backticks/tildes, group 2 what follows.
FENCE_LINE_RE = re.compile(r"^(`{3,}|~{3,})(.*)$")

# An inline markdown link or image target: group 1 is `](`, group 2 the destination, group 3 an optional title and `)`.
MD_LINK_TARGET_RE = re.compile(r'(\]\()([^)\s]+)((?:\s+"[^"]*")?\))')

# An inline code span (CommonMark: a run of N backticks, closed by the next run of exactly N), so a link-looking snippet
# inside one is left as written.
INLINE_CODE_RE = re.compile(r"(?<!`)(`+)(?!`).+?(?<!`)\1(?!`)")

# Page-level H1, prepended to sub-pages so `## 2024.5.0` reads as
# `# Changelog\n\n## 2024.5.0` instead of context-free.
H1_PREFIX_RE = re.compile(r"^(#\s+[^\n]+)\n+", re.MULTILINE)

# Mintlify changelog pages wrap each release in `<Update label="v4.0.9" ...>`
# — a real per-release boundary the generic H2/H3 splitter can't see (it's a
# component tag, not a heading), so releases instead get chopped on their
# repeating internal `## New Contributors` / `## What's Changed` H2s and
# collide under the same handful of slugs. Split on this instead when present.
UPDATE_TAG_RE = re.compile(r'<Update\s+label="([^"]+)"')

# Slug marker for a changelog-release sub-page — post.service reads this back
# to tag the manifest entry `tier = "changelog"` (see domain.split_monolith's
# changelog branch); corpus_load filters on that tier.
CHANGELOG_RELEASE_MARKER = "-changelog-release-"

# The `<Update label="v3.2.3">` tag as the FIRST line after a split child's `# Parent`, where either quote may be used (the
# splitter's UPDATE_TAG_RE above is the double-quoted form it cuts a changelog on). Group 1 is the label.
UPDATE_LABEL_RE = re.compile(r"""<Update\s+label=["']([^"']+)["']""")

# A sub-split child's own heading (`## x`, `### x`, `#### x`), right after the `# Parent` it was filed under: group 1 is its
# text, minus any closing `#`s.
CHILD_HEADING_RE = re.compile(r"^#{2,4}\s+(.+?)\s*#*\s*$")


# A run of anything a slug does not keep.
NON_SLUG_RE = re.compile(r"[^a-z0-9]+")

# The first line of a numbered list item (`1. x`, `2) x`): not a sentence worth naming a page after.
NUMBERED_ITEM_RE = re.compile(r"^\d+[.)]\s+")

# Markdown's own marks in a line of text (emphasis, code, strikethrough, `#`), taken off before it is used as a title.
MD_MARKS_RE = re.compile(r"[*_`~#]+")

# What makes a line prose: a word of three letters or more.
WORD_RE = re.compile(r"[a-zA-Z]{3,}")

# Emphasis and code marks inside a heading (`**x**`, `` `x` ``).
INLINE_MARKS_RE = re.compile(r"[`*]+")

# A character a converter escaped because Markdown would read it as markup: `all\_inner\_texts`. Group 1 is the character.
ESCAPED_CHAR_RE = re.compile(r"\\([\\_*\[\]()#+.!|<>-])")

# Any run of whitespace (a title is one line, one space apart).
WHITESPACE_RE = re.compile(r"\s+")

# The `-` / `_` between the words of a URL folder or file name.
WORD_SEPARATOR_RE = re.compile(r"[-_]+")

# A file name's extension at the end of a URL segment (`.html`, `.md`, `.php`).
FILE_EXTENSION_RE = re.compile(r"\.\w{1,5}$")
