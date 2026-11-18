"""Tier 1 — pre-compiled regex for manifest and HTML detection."""
from __future__ import annotations

import re


FENCE_RE = re.compile(r"(?m)^```")
URL_LINE_RE = re.compile(r"(?m)^URL:\s+https?://")
MD_POINTER_RE = re.compile(r"(?m)^Markdown:\s+https?://\S+\.md\s*$")
HTML_START_RE = re.compile(r"\s*(?:<!doctype\s+html|<html[\s>])", re.IGNORECASE)

# A paginated bundle (GitBook's: "part 1 of 2 … Next part: <url>") says where its next part is, in a block quote at the very top; every
# part opens with such a banner, which is about the pagination and not about the docs.
NEXT_PART_RE = re.compile(r"(?mi)^>\s*Next part:\s*(\S+)")
PART_BANNER_RE = re.compile(r"\A(?:[ \t]*>[^\n]*\n)+[ \t]*\n?")
PART_BANNER_MARK_RE = re.compile(r"(?i)\bpart \d+ of \d+\b")

# A run of anything a slug does not keep.
NON_SLUG_RE = re.compile(r"[^a-z0-9]+")
