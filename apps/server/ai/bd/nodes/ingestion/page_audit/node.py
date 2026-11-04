"""Read-only LangGraph node for structured auditing of one raw page."""
from __future__ import annotations
from . import domain, params
from .... import graphs

import hashlib
import inspect
from collections.abc import Awaitable, Callable
from typing import Any, Protocol


PageReader = Callable[[str], str | None | Awaitable[str | None]]


class PageAuditor(Protocol):
    async def ainvoke(self, input: dict[str, Any]) -> dict[str, Any]: ...


async def audit_page(
    state: graphs.ingestion.state.PageAuditState,
    read_page: PageReader,
    auditor: PageAuditor,
) -> dict:
    _, _, page_number, object_key, pictures = domain.validate_request(state)
    markdown = read_page(object_key)
    if inspect.isawaitable(markdown):
        markdown = await markdown
    if not isinstance(markdown, str):
        raise ValueError(f"page {page_number} could not be read as Markdown text")
    if len(markdown) > params.MAX_PAGE_MARKDOWN_CHARS:
        raise ValueError(
            f"page {page_number} exceeds the auditor limit; split or escalate it instead of truncating"
        )

    result = await auditor.ainvoke({
        "messages": [{
            "role": "user",
            "content": domain.user_payload(page_number, pictures, markdown),
        }],
    })
    if not isinstance(result, dict) or "structured_response" not in result:
        raise ValueError("page auditor returned no structured PageAssessment")

    assessment, warnings = domain.sanitize_assessment(
        result["structured_response"],
        page_number = page_number,
        pictures = pictures,
        markdown = markdown,
    )
    return {
        "assessment": assessment.model_dump(mode = "json"),
        "source_sha256": hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
        "status": "complete",
        "warnings": warnings,
    }
