"""audit_batch errors."""
from __future__ import annotations


class ModelCallLimitReachedError(ValueError):
    """The page auditor stopped at its configured model-call limit."""
