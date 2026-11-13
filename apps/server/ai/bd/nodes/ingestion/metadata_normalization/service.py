"""Read bounded front matter and persist evidence-grounded book metadata."""
from __future__ import annotations
import domains
from . import domain
from .... import graphs, skills

import hashlib
import inspect
import json
from collections.abc import Awaitable, Callable
from typing import Any, Protocol


PageReader = Callable[[str], str | None | Awaitable[str | None]]
ArtifactWriter = Callable[[str, str, str], Any]


class Normalizer(Protocol):
    async def ainvoke(
        self,
        input: dict[str, Any],
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


async def normalize_metadata(
    state: graphs.ingestion.states.IngestionRunState,
    read_page: PageReader,
    write_artifact: ArtifactWriter,
    normalizer: Normalizer,
) -> dict:
    """Normalize metadata from a bounded front-matter sample or retain the source title for review."""
    sources = []
    audits = {page["page"]: page for page in state["page_audits"]}
    remaining = domains.bd.ingestion.ai.params.MAX_METADATA_SOURCE_CHARS
    for page_ref in state["source_page_refs"][:domains.bd.ingestion.ai.params.MAX_METADATA_SOURCE_PAGES]:
        markdown = read_page(page_ref["key"])
        if inspect.isawaitable(markdown):
            markdown = await markdown
        if not isinstance(markdown, str):
            continue
        expected_sha256 = audits[page_ref["page"]]["source_sha256"]
        if hashlib.sha256(markdown.encode("utf-8")).hexdigest() != expected_sha256:
            raise ValueError(f"source page {page_ref['page']} changed before metadata normalization")
        excerpt = markdown[:remaining]
        if excerpt:
            sources.append({"page_number": page_ref["page"], "markdown": excerpt})
            remaining -= len(excerpt)
        if remaining <= 0:
            break

    source_metadata = state.get("source_metadata", {})
    filename = source_metadata.get("filename")
    source_values = domain.metadata_values(source_metadata)
    if isinstance(filename, str) and filename.strip():
        source_values.append(filename)
    payload = {
        "task": "Normalize display title and authors using only supplied evidence.",
        "source_metadata": source_metadata,
        "front_matter_pages": sources,
    }
    proposal = None
    proposal_validated = False
    errors = []
    try:
        run_id = state.get("agentic_run_id")
        if not isinstance(run_id, str):
            raise ValueError("the ingestion run has no agentic_run_id")
        with domains.settings.llm_counter.service.llm_context(
            ns = "bd", run_id = run_id, node_id = "metadata_normalizer",
        ):
            response = await normalizer.ainvoke({"messages": [{
                "role": "user",
                "content": json.dumps(payload, ensure_ascii = False),
            }], "files": skills.service.files_for("metadata_normalizer")}, config = {
                "callbacks": [domains.settings.llm_counter.service.LLMCounterCallback()],
            })
        proposal = domain.extract_proposal(response)
        domain.validate_proposal(proposal, sources, source_values)
        proposal_validated = True
    except Exception as error:
        errors.append(str(error)[:500])

    normalized_metadata = domain.resolve_metadata(
        proposal,
        source_metadata,
        sources,
        proposal_validated = proposal_validated,
    )
    proposal_payload = proposal.model_dump(mode = "json") if proposal is not None else None
    artifact = {
        **normalized_metadata,
        "proposal": proposal_payload,
        "errors": errors,
    }
    content = json.dumps(artifact, ensure_ascii = False, separators = (",", ":"), sort_keys = True)
    key = domains.bd.ingestion.storage.keys.agentic_book_metadata_key(
        state["book_id"], state["agentic_run_id"],
    )
    result = write_artifact(key, content, "application/json")
    if inspect.isawaitable(result):
        await result
    return {
        "book_metadata": normalized_metadata,
        "book_metadata_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "status": "metadata_normalized",
    }
