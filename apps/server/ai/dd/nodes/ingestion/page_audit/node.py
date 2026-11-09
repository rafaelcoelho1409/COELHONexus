"""Read-only LangGraph node for one evidence-grounded page audit."""
from __future__ import annotations
from . import domain, params
from .... import agents

from typing import Any


async def audit_page(
    framework_slug: str,
    page: dict[str, Any],
    markdown: str,
    auditor: Any,
    *,
    config: dict[str, Any] | None = None,
) -> tuple[agents.schemas.PageAssessment, list[str]]:
    if len(markdown) > params.MAX_PAGE_MARKDOWN_CHARS:
        raise ValueError(
            f"page {page['idx']} exceeds the {params.MAX_PAGE_MARKDOWN_CHARS}-character audit limit; "
            "split or escalate it instead of truncating"
        )

    request = domain.user_payload(
        framework_slug,
        page["idx"],
        page["url"],
        page["title"],
        markdown,
        domain.candidates(markdown),
    )
    assessment = await agents.service.ask_chat_model(
        auditor,
        agents.schemas.PageAssessment,
        agents.ingestion.page_auditor.domain.system_prompt(),
        request,
        attempts = params.MAX_ATTEMPTS,
        wait_base_s = params.RETRY_WAIT_BASE_S,
        config = config,
    )
    return domain.sanitize_assessment(assessment, markdown = markdown)
