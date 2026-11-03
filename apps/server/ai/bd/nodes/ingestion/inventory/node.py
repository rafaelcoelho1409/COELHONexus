"""Read-only graph adapter for deterministic inventory of raw Markdown pages."""
from __future__ import annotations
from .... import graphs
from . import domain

import hashlib
import inspect
from collections.abc import Awaitable, Callable


PageReader = Callable[[str], str | None | Awaitable[str | None]]


async def inventory_pages(
    state: graphs.ingestion.state.IngestionInventoryState,
    read_page: PageReader,
) -> dict:
    _, _, page_refs = domain.validate_request(state)
    inventory: list[graphs.ingestion.state.PageInventoryResult] = []
    for page_ref in page_refs:
        markdown = read_page(page_ref["key"])
        if inspect.isawaitable(markdown):
            markdown = await markdown
        if not isinstance(markdown, str):
            raise ValueError(f"page {page_ref['page']} could not be read as Markdown text")

        assessment = domain.assess_page(page_ref["page"], markdown, page_ref["pictures"])
        inventory.append({
            "page": page_ref["page"],
            "key": page_ref["key"],
            "source_sha256": hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
            "markdown_chars": len(markdown),
            "pictures": page_ref["pictures"],
            "assessment": assessment.model_dump(mode = "json"),
        })

    return {
        "inventory": inventory,
        "inventory_summary": domain.inventory_summary(inventory),
        "status": "complete",
    }
