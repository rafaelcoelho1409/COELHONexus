"""LangGraph node that asks for and validates minimal Markdown repairs."""
from __future__ import annotations
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
    result = await repairer.ainvoke({
        "messages": [{"role": "user", "content": prompt}],
        "files": files,
    }, config = config)
    if not isinstance(result, dict) or "structured_response" not in result:
        raise ValueError("page repair specialist returned no structured PagePatchBatch")

    return domain.apply_patches(
        markdown,
        source_sha256,
        assessment,
        result["structured_response"],
    )
