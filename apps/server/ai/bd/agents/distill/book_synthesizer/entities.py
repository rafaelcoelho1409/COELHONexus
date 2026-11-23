"""Runtime dependencies for structured and JSON-text book synthesis agents."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen = True, slots = True)
class BookSynthesizer:
    structured: Any
    text_fallback: Any
