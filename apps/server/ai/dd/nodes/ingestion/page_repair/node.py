"""LangGraph node that asks for and validates minimal Markdown repairs."""
from __future__ import annotations
import domains
from . import domain, params
from .. import page_audit
from .... import agents, tools

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
    config: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    if len(markdown) > page_audit.params.MAX_PAGE_MARKDOWN_CHARS:
        raise ValueError(
            f"page {page['idx']} exceeds the {page_audit.params.MAX_PAGE_MARKDOWN_CHARS}-character repair limit; "
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
    # The page and its audit also go in the agent's files, where its `check_patch` tool reads them.
    request = {
        "messages": [{"role": "user", "content": prompt}],
        "files": {
            tools.ingestion.check_patch.params.PAGE_PATH: agents.service.file(markdown),
            tools.ingestion.check_patch.params.ASSESSMENT_PATH: agents.service.file(json.dumps(assessment, ensure_ascii = False)),
        },
    }
    # Retry transient provider failures and occasional non-structured answers in one bounded loop.
    result = await domains.settings.chat.service.resilient_ainvoke(
        lambda: repairer.ainvoke(request, config = config),
        attempts = params.MAX_ATTEMPTS,
        wait_base_s = params.RETRY_WAIT_BASE_S,
        accept = lambda value: agents.domain.structured_or_recovered(value, agents.schemas.PagePatchBatch) is not None,
    )
    batch = agents.domain.structured_or_recovered(result, agents.schemas.PagePatchBatch)
    if batch is not None:
        return domain.apply_patches(markdown, source_sha256, assessment, batch)
    raise ValueError(
        f"page repair specialist ended {params.MAX_ATTEMPTS} times without a structured PagePatchBatch "
        f"for page {page['idx']}"
    )
