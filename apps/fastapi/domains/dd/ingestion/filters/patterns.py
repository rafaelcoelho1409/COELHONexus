from __future__ import annotations

import re

from .params import DEFAULT_EXCLUDE_PATH_PATTERNS


# Localization paths dropped when target language is English-biased.
NON_TARGET_LANGUAGE_PATH_RE = re.compile(
    r"/(zh|cn|ja|ko|pt|fr|de|es|ru|it|tr|pl|nl|vi|th|ar|id|hi)(-[a-z]{2})?(/|$)",
    re.IGNORECASE,
)


DEFAULT_EXCLUDE_RE = re.compile(
    "|".join(f"(?:{p})" for p in DEFAULT_EXCLUDE_PATH_PATTERNS),
    re.IGNORECASE,
)
