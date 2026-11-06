"""Read-only LangGraph node for one evidence-grounded page audit."""
from __future__ import annotations
import domains
from . import detectors, domain, params
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
    config: dict[str, Any] | None = None,
) -> tuple[agents.schemas.PageAssessment, list[str]]:
    if len(markdown) > params.MAX_PAGE_MARKDOWN_CHARS:
        raise ValueError(
            f"page {page['idx']} exceeds the {params.MAX_PAGE_MARKDOWN_CHARS}-character audit limit; "
            "split or escalate it instead of truncating"
        )

    request = {
        "messages": [{
            "role": "user",
            "content": domain.user_payload(
                framework_slug,
                page["idx"],
                page["url"],
                page["title"],
                markdown,
                detectors.candidates(markdown),
            ),
        }],
    }
    # Retry transient provider failures and occasional non-structured answers in one bounded loop.
    result = await domains.settings.chat.service.resilient_ainvoke(
        lambda: auditor.ainvoke(request, config = config),
        attempts = params.MAX_ATTEMPTS,
        wait_base_s = params.RETRY_WAIT_BASE_S,
        accept = lambda value: agents.domain.structured_or_recovered(value, agents.schemas.PageAssessment) is not None,
    )
    assessment = agents.domain.structured_or_recovered(result, agents.schemas.PageAssessment)
    if assessment is not None:
        return domain.sanitize_assessment(assessment, markdown = markdown)
    raise ValueError(
        f"page auditor ended {params.MAX_ATTEMPTS} times without a structured PageAssessment for page {page['idx']}"
    )
