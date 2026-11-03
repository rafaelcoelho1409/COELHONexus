from __future__ import annotations

import re


CODE_FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
MARKDOWN_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})[ \t]+(.+?)\s*#*\s*$")
