"""repair_pages errors."""
from __future__ import annotations


class ModelCallLimitReachedError(ValueError):
    """A specialist stopped at its configured model-call limit."""
