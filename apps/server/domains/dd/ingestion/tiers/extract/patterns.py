"""Pre-compiled regexes for the HTML→Markdown pipeline."""
from __future__ import annotations

import re


# A `#…` link: a heading that is a link to itself (mdBook's `<h1><a class="header" href="#intro">`).
SELF_LINK_HREF_RE = re.compile(r"^#")

# The class of a callout's title element: `admonition-title`, `callout_title`, `title`.
CALLOUT_TITLE_CLASS_RE = re.compile(r"(^|[-_])title($|[-_])", re.IGNORECASE)

# Any run of whitespace, and three or more line breaks in a row.
WHITESPACE_RE = re.compile(r"\s+")
BLANK_LINES_RE = re.compile(r"\n{3,}")

# What is not a lowercase letter or a digit: a heading and a `<title>` are compared as the words they are made of.
NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

# A fenced code block, whole (backticks or tildes; the closing fence is the opening one's kind and at least as long).
FENCED_BLOCK_RE = re.compile(r"^(`{3,}|~{3,}).*?^\1[ \t]*$", re.MULTILINE | re.DOTALL)

# An ATX heading line: group 1 is its `#`s, group 2 its text without the closing `#`s.
ATX_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$", re.MULTILINE)

# A level-1 heading line.
H1_LINE_RE = re.compile(r"^# \S", re.MULTILINE)

# A class that names a code block's language (`language-python`, `highlight-source-go`, `lang_rust`): group 1 is the name.
LANGUAGE_CLASS_RE = re.compile(
    r"^(?:language|lang|highlight-source|highlight)[-_]([A-Za-z][A-Za-z0-9+#_-]{0,19})$"
)

# A `data-language` value that is a language name and not something else (a sentence, a path).
LANGUAGE_NAME_RE = re.compile(r"[a-z][a-z0-9+#_-]{0,19}")

# The start of a shell session or a command (`$ go run x.go`, `npm install typescript`, `#!/bin/sh`, `>>> import x`): not source in
# the docs' language, so a block that starts like this is not given the catalog's default language.
SHELL_START_RE = re.compile(
    r"^\s*(?:\$|#!|>>>|%|(?:sudo|pip3?|npm|npx|yarn|pnpm|brew|apt(?:-get)?|go (?:get|install|run|build|mod|test)|git|cd|mkdir|curl|wget|export|docker|make)\s)"
)
