"""Read bounded front matter and persist evidence-grounded book metadata."""
from __future__ import annotations
import domains
from . import patterns
from .... import agents, graphs, skills

import hashlib
import inspect
import json
import re
import unicodedata
from collections.abc import Awaitable, Callable
from pathlib import PurePath
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
    state: graphs.ingestion.state.IngestionRunState,
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
    source_values = _metadata_values(source_metadata)
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
        if not isinstance(response, dict) or "structured_response" not in response:
            raise ValueError("metadata normalizer returned no structured BookMetadataProposal")
        proposal = agents.schemas.BookMetadataProposal.model_validate(response["structured_response"])
        for evidence in proposal.evidence:
            if evidence.source == agents.schemas.EvidenceSource.PAGE_MARKDOWN:
                matching_sources = [
                    source for source in sources if source["page_number"] == evidence.page_number
                ]
                if not matching_sources or evidence.excerpt not in matching_sources[0]["markdown"]:
                    raise ValueError("metadata evidence is not present in the supplied front matter")
            elif evidence.source == agents.schemas.EvidenceSource.BOOK_METADATA:
                if not any(evidence.excerpt in value for value in source_values):
                    raise ValueError("metadata evidence is not present in supplied book metadata")
            else:
                raise ValueError("metadata evidence must come from supplied front matter or book metadata")
        evidence_text = " ".join(evidence.excerpt for evidence in proposal.evidence)
        if not _metadata_value_is_grounded(proposal.title, evidence_text):
            raise ValueError("normalized title is not supported by the supplied evidence")
        if proposal.subtitle and not _metadata_value_is_grounded(proposal.subtitle, evidence_text):
            raise ValueError("normalized subtitle is not supported by the supplied evidence")
        if any(not _metadata_value_is_grounded(author, evidence_text) for author in proposal.authors):
            raise ValueError("a normalized author is not supported by the supplied evidence")
        proposal_validated = True
    except Exception as error:
        errors.append(str(error)[:500])

    fallback_title = _fallback_title(source_metadata, sources)
    confident = (
        proposal_validated
        and proposal is not None
        and proposal.confidence >= domains.bd.ingestion.ai.params.METADATA_ACCEPT_CONFIDENCE
    )
    fallback_subtitle = source_metadata.get("subtitle")
    if not isinstance(fallback_subtitle, str):
        fallback_subtitle = None
    fallback_authors = source_metadata.get("authors", [])
    if isinstance(fallback_authors, str):
        fallback_authors = [fallback_authors] if fallback_authors.strip() else []
    if not isinstance(fallback_authors, list):
        fallback_authors = []
    fallback_authors = [author.strip() for author in fallback_authors if isinstance(author, str) and author.strip()]
    normalized_metadata = {
        "title": proposal.title if confident else fallback_title,
        "subtitle": proposal.subtitle if confident else fallback_subtitle,
        "authors": proposal.authors if confident else fallback_authors,
        "confidence": proposal.confidence if proposal_validated and proposal is not None else 0.0,
        "review_required": not confident,
        "source_filename": filename,
        "rationale": (
            proposal.rationale
            if proposal_validated and proposal is not None
            else "No validated metadata proposal was available."
        ),
    }
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
        "metadata_proposal": proposal_payload or {},
        "book_metadata": normalized_metadata,
        "book_metadata_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "status": "metadata_normalized",
    }


def _metadata_values(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [entry for entry in value if isinstance(entry, str)]
    if isinstance(value, dict):
        return [item for entry in value.values() for item in _metadata_values(entry)]
    return []


def _fallback_title(metadata: dict[str, object], sources: list[dict[str, Any]]) -> str:
    filename = metadata.get("filename") or metadata.get("source_filename")
    filename_title = _filename_title(filename)
    supplied_title = metadata.get("title")
    if isinstance(supplied_title, str) and supplied_title.strip():
        title = " ".join(supplied_title.split())
        if not _matches_filename_label(title, filename_title):
            return title

    for source in sources:
        if source.get("page_number") != 1:
            continue
        markdown = source.get("markdown")
        if not isinstance(markdown, str):
            continue
        heading = re.search(r"(?m)^\s{0,3}#{1,2}\s+([^\n]{3,180})\s*#*\s*$", markdown)
        if heading:
            candidate = " ".join(heading.group(1).split())
            if candidate.casefold() not in {"introduction", "contents", "table of contents", "preface"}:
                return candidate

    return _strip_source_site_tags(filename_title) if filename_title else "Untitled book"


def _filename_title(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    title = PurePath(value.strip().replace("\\", "/")).stem
    return re.sub(r"\s+", " ", title.replace("_", " ")).strip()


def _strip_source_site_tags(title: str) -> str:
    """Drop trailing "(z-library.sk, 1lib.sk)"-style download-site tags from a filename-derived title."""
    cleaned = patterns.SOURCE_SITE_TAG_RE.sub("", title)
    return re.sub(r"\s+", " ", cleaned).strip() or title


def _matches_filename_label(value: str, filename_title: str) -> bool:
    normalize = lambda text: re.sub(r"[^\w]+", " ", text).casefold().split()
    if not filename_title:
        return False
    # A dot inside a title ("z-lib.sk)") is not a file extension, so only a real document suffix is removed.
    value_without_suffix = patterns.DOCUMENT_SUFFIX_RE.sub("", value.strip())
    label = normalize(filename_title)
    return normalize(value) == label or normalize(value_without_suffix) == label


def _metadata_value_is_grounded(value: str, evidence_text: str) -> bool:
    normalize = lambda text: unicodedata.normalize("NFKC", text).replace("\u00ad", "").casefold()
    value_tokens = re.findall(r"[\w]+", normalize(value))
    evidence_tokens = re.findall(r"[\w]+", normalize(evidence_text))
    return bool(value_tokens) and all(token in evidence_tokens for token in value_tokens)
