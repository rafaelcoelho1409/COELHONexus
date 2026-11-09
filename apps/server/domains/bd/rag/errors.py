"""Book retrieval workflow errors."""
from __future__ import annotations


class IndexCancelled(Exception):
    """Raised internally when a user requests a retrieval-index stop."""
