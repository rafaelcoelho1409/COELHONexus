from __future__ import annotations


class IngestCancelled(Exception):
    """Raised by tier modules on cancel-flag set. Dispatch runs cleanup."""
