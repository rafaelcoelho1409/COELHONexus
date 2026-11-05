"""LangGraph node that asks for and validates minimal Markdown repairs."""
from __future__ import annotations
import domains
from . import domain
from ..page_audit import params as audit_params

import json
from typing import Any, Protocol


class PageRepairer(Protocol):
    async def ainvoke(
        self,
        input: dict[str, Any],
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


async def repair_page(
    framework_slug: str,
    page: dict[str, Any],
    markdown: str,
    source_sha256: str,
    assessment: dict[str, Any],
    repairer: PageRepairer,
    *,
    files: dict[str, Any],
    config: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    if len(markdown) > audit_params.MAX_PAGE_MARKDOWN_CHARS:
        raise ValueError(
            f"page {page['idx']} exceeds the {audit_params.MAX_PAGE_MARKDOWN_CHARS}-character repair limit; "
            "split or escalate it instead of truncating"
        )

    prompt = json.dumps({
        "task": "Propose safe minimal repairs for this audited documentation page.",
        "framework_slug": framework_slug,
        "page_index": page["idx"],
        "source_sha256": source_sha256,
        "assessment": assessment,
        "markdown": markdown,
    }, ensure_ascii = False)
    request = {
        "messages": [{"role": "user", "content": prompt}],
        "files": files,
    }
    # Retry transient provider failures and occasional non-structured answers in one bounded loop.
    result = await domains.settings.chat.service.resilient_ainvoke(
        lambda: repairer.ainvoke(request, config = config),
        attempts = audit_params.MAX_ATTEMPTS,
        wait_base_s = audit_params.RETRY_WAIT_BASE_S,
        accept = lambda value: isinstance(value, dict) and value.get("structured_response") is not None,
    )
    if isinstance(result, dict) and result.get("structured_response") is not None:
        return domain.apply_patches(
            markdown,
            source_sha256,
            assessment,
            result["structured_response"],
        )
    raise ValueError(
        f"page repair specialist ended {audit_params.MAX_ATTEMPTS} times without a structured PagePatchBatch "
        f"for page {page['idx']}"
    )
