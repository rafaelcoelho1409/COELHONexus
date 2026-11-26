"""synthesize_chapter errors."""
from __future__ import annotations


class ChapterSynthesisOutputError(ValueError):
    """Raised after structured chapter output stays invalid through response repair attempts."""
