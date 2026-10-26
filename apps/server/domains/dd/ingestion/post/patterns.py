from __future__ import annotations

import re


# Mintlify/Streamlit llms-full.txt boundary marker.
SOURCE_LINE_RE = re.compile(
    r'^Source:\s+(https?://\S+)\s*$', re.MULTILINE,
)


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
