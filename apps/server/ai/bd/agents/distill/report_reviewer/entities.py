"""Runtime dependencies for the structured and compatibility review agents."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen = True, slots = True)
class ReportReviewer:
    structured: Any
    text_fallback: Any
