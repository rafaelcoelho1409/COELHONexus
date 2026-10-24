from __future__ import annotations

import re


# `data:image/png;base64,iVBORw...` → (mime, optional encoding, payload).
DATA_URL_RE = re.compile(
    r"^data:(?P<mime>[\w/\-+.]+)"
    r"(?:;[\w-]+=[\w-]+)*"
    r"(?:;(?P<enc>base64))?,"
    r"(?P<data>.*)$",
    re.DOTALL,
)


# `![alt](url)`. Doesn't handle nested brackets / escaped parens (rare in docs).
MD_IMG_RE = re.compile(
    r'!\[(?P<alt>[^\]]*)\]'
    r'\((?P<url>[^)\s]+)'
    r'(?:\s+"[^"]*")?'
    r'\)',
)


# Opening tag only; attributes pulled via HTML_ATTR_RE. Forgiving fast-path,
# not a full parser — enough for Tier 1 markdown.
MD_HTML_TAG_RE = re.compile(
    r'<(?P<tag>img|video|audio|source)\b(?P<attrs>[^>]*)>',
    re.IGNORECASE,
)


HTML_ATTR_RE = re.compile(
    r"""(?P<attr>\b(?:src|data-src|poster|srcset))\s*=\s*"""
    r"""(?P<q>['"])(?P<value>.*?)(?P=q)""",
    re.IGNORECASE | re.DOTALL,
)
