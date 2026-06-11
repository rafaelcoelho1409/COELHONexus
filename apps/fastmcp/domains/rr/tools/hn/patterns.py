"""Pre-compiled regexes for the HN tool — per docs/CODE-CONVENTIONS.md §2.

Extracts an `arxiv_id` from a story's external URL when the link points at
arxiv.org or huggingface.co/papers/<arxiv_id>. That's THE cross-source dedup
key — when the same paper appears in arxiv search results AND a hot HN post,
the agent can merge by `arxiv_id` in Neo4j and graft `points` + `num_comments`
onto the arxiv record.
"""
from __future__ import annotations

import re


# Matches: arxiv.org/abs/2406.12345  · arxiv.org/abs/2406.12345v2  · /pdf/...
ARXIV_URL_RE = re.compile(
    r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5}(?:v\d+)?)",
    re.IGNORECASE,
)


# Matches: huggingface.co/papers/2406.12345 (HF embeds the arxiv id directly)
HF_PAPERS_URL_RE = re.compile(
    r"huggingface\.co/papers/(\d{4}\.\d{4,5}(?:v\d+)?)",
    re.IGNORECASE,
)
