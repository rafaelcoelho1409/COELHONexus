"""URL & language filters shared across Tier 3 / Tier 4."""
from .constants import (
    DEFAULT_DENY_PATTERNS,
    DEFAULT_EXCLUDE_PATH_PATTERNS,
    LANGUAGE_PATH_MAP,
    NON_TARGET_LANGUAGE_PATH_RE,
    POLYGLOT_FRAMEWORKS,
    _DEFAULT_EXCLUDE_RE,
)
from .service import (
    build_language_filter,
    is_polyglot,
    passes_path_filter,
    same_host,
    should_keep,
)

__all__ = [
    # constants
    "DEFAULT_DENY_PATTERNS",
    "DEFAULT_EXCLUDE_PATH_PATTERNS",
    "LANGUAGE_PATH_MAP",
    "NON_TARGET_LANGUAGE_PATH_RE",
    "POLYGLOT_FRAMEWORKS",
    "_DEFAULT_EXCLUDE_RE",
    # functions
    "build_language_filter",
    "is_polyglot",
    "passes_path_filter",
    "same_host",
    "should_keep",
]
