"""Book and operation authorization shared by BD MCP components."""
from __future__ import annotations

from fastmcp.server.dependencies import get_access_token

from ..tools.search import domain, params


async def authorize_book(
    book_id: str,
    trusted_in_process: bool,
    *,
    required_scope: str | None = None,
    mutation: bool = False,
) -> None:
    """Require an allowed book and, for external callers, the requested scope."""
    if not domain.safe_segment(book_id):
        raise ValueError("book_id must be a safe path segment")
    if trusted_in_process:
        return
    if params.MCP_PROFILE == "local_demo":
        if mutation:
            raise PermissionError("BD MCP demo access is read-only")
        if not domain.book_is_allowed(book_id, params.DEMO_BOOK_IDS):
            raise PermissionError("This book is not available in the local demo corpus")
        return

    token = get_access_token()
    if token is None:
        raise PermissionError("Authentication is required")
    if book_id not in domain.token_book_ids(token.claims):
        raise PermissionError("The authenticated principal cannot access this book")
    if required_scope is not None and (
        not required_scope or required_scope not in token.scopes
    ):
        raise PermissionError("The authenticated principal lacks the required BD scope")
