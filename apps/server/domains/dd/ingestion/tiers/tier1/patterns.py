"""Tier 1 — pre-compiled regex for manifest and HTML detection."""
from __future__ import annotations

import re


FENCE_RE = re.compile(r"(?m)^```")
URL_LINE_RE = re.compile(r"(?m)^URL:\s+https?://")
MD_POINTER_RE = re.compile(r"(?m)^Markdown:\s+https?://\S+\.md\s*$")
HTML_START_RE = re.compile(r"\s*(?:<!doctype\s+html|<html[\s>])", re.IGNORECASE)

# A run of anything a slug does not keep.
NON_SLUG_RE = re.compile(r"[^a-z0-9]+")
