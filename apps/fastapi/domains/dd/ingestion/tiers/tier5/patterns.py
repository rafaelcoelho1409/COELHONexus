"""Tier 5 — pre-compiled regex (locale path filter + slug normalizer)."""
from __future__ import annotations

import re


# Localization subtrees we drop unless explicitly named "en" — most repos
# canonicalize on English.
NON_EN_LOCALE_RE = re.compile(
    r"(^|/)(?!en/)([a-z]{2}|[a-z]{2}-[A-Z]{2})/",
)

MD_EXT_RE = re.compile(r"\.(md|mdx|markdown)$", re.IGNORECASE)
NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
