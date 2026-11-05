"""Read-only LangGraph node for one evidence-grounded page audit."""
from __future__ import annotations
from . import domain, params
from .... import agents

from typing import Any, Protocol


class PageAuditor(Protocol):
    async def ainvoke(
        self,
        input: dict[str, Any],
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


async def audit_page(
    framework_slug: str,
    page: dict[str, Any],
    markdown: str,
    auditor: PageAuditor,
    *,
    files: dict[str, Any],
    config: dict[str, Any] | None = None,
) -> tuple[agents.schemas.PageAssessment, list[str]]:
    if len(markdown) > params.MAX_PAGE_MARKDOWN_CHARS:
        raise ValueError(
            f"page {page['idx']} exceeds the {params.MAX_PAGE_MARKDOWN_CHARS}-character audit limit; "
            "split or escalate it instead of truncating"
        )

    result = await auditor.ainvoke({
        "messages": [{
            "role": "user",
            "content": domain.user_payload(
                framework_slug,
                page["idx"],
                page["url"],
                page["title"],
                markdown,
            ),
        }],
        "files": files,
    }, config = config)
    if not isinstance(result, dict) or "structured_response" not in result:
        raise ValueError("page auditor returned no structured PageAssessment")

    return domain.sanitize_assessment(result["structured_response"], markdown = markdown)
