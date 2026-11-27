"""Resource handlers for adaptive BD Ask book readiness."""
from __future__ import annotations

from .... import tools


class AdaptiveAskResources:
    """Expose the current pipeline snapshot before a book question is answered."""

    async def readiness(self, book_id: str) -> dict:
        """Return readiness for one validated BD book identifier."""
        return await tools.ask.adaptive.service.read_readiness(book_id)
