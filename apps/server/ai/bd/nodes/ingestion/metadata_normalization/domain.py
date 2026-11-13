"""Validation and recovery of evidence-backed metadata proposals."""
from __future__ import annotations
import domains
from . import patterns
from .... import agents

import unicodedata
from collections.abc import Iterator
from json import JSONDecodeError, JSONDecoder
from pathlib import PurePath
from typing import Any

from pydantic import ValidationError


def extract_proposal(response: Any) -> agents.schemas.BookMetadataProposal:
    """Validate structured output or recover a JSON proposal from the last assistant message."""
    structured = response.get("structured_response") if isinstance(response, dict) else None
    if structured is not None:
        return agents.schemas.BookMetadataProposal.model_validate(structured)

    messages = response.get("messages") if isinstance(response, dict) else None
    content = _last_assistant_content(messages)
    if content is None:
        raise ValueError("metadata normalizer returned no structured response or assistant message")

    last_error: ValidationError | JSONDecodeError | None = None
    for value in _json_objects(content):
        try:
            return agents.schemas.BookMetadataProposal.model_validate(value)
        except ValidationError as error:
            last_error = error

    if last_error is not None:
        raise ValueError("metadata normalizer returned JSON that did not match its response schema") from last_error
    raise ValueError("metadata normalizer returned no structured response and no JSON object")


def _last_assistant_content(messages: Any) -> str | None:
    if not isinstance(messages, list):
        return None
    for message in reversed(messages):
        if isinstance(message, dict):
            role = message.get("role") or message.get("type")
            content = message.get("content")
        else:
            role = getattr(message, "type", None)
            content = getattr(message, "content", None)
        if role not in {"assistant", "ai", "AIMessage"}:
            continue
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [
                block["text"]
                for block in content
                if isinstance(block, dict) and isinstance(block.get("text"), str)
            ]
            if parts:
                return "\n".join(parts)
    return None


def _json_objects(content: str) -> Iterator[dict[str, Any]]:
    decoder = JSONDecoder()
    for start, character in enumerate(content):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(content[start:])
        except JSONDecodeError:
            continue
        if isinstance(value, dict):
            yield value


def metadata_values(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [entry for entry in value if isinstance(entry, str)]
    if isinstance(value, dict):
        return [item for entry in value.values() for item in metadata_values(entry)]
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
        heading = patterns.TITLE_HEADING_RE.search(markdown)
        if heading:
            candidate = " ".join(heading.group(1).split())
            if candidate.casefold() not in {"introduction", "contents", "table of contents", "preface"}:
                return candidate

    return _strip_source_site_tags(filename_title) if filename_title else "Untitled book"


def _filename_title(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    title = PurePath(value.strip().replace("\\", "/")).stem
    return patterns.WHITESPACE_RE.sub(" ", title.replace("_", " ")).strip()


def _strip_source_site_tags(title: str) -> str:
    """Drop trailing download-site tags from a filename-derived title."""
    cleaned = patterns.SOURCE_SITE_TAG_RE.sub("", title)
    return patterns.WHITESPACE_RE.sub(" ", cleaned).strip() or title


def _matches_filename_label(value: str, filename_title: str) -> bool:
    def normalize(text: str) -> list[str]:
        return patterns.NON_WORD_RE.sub(" ", text).casefold().split()

    if not filename_title:
        return False
    # A dot in a title is not a file extension, so only remove a real document suffix.
    value_without_suffix = patterns.DOCUMENT_SUFFIX_RE.sub("", value.strip())
    label = normalize(filename_title)
    return normalize(value) == label or normalize(value_without_suffix) == label


def _metadata_value_is_grounded(value: str, evidence_text: str) -> bool:
    def normalize(text: str) -> str:
        return unicodedata.normalize("NFKC", text).replace("\u00ad", "").casefold()

    value_tokens = patterns.WORD_RE.findall(normalize(value))
    evidence_tokens = patterns.WORD_RE.findall(normalize(evidence_text))
    return bool(value_tokens) and all(token in evidence_tokens for token in value_tokens)


def validate_proposal(
    proposal: agents.schemas.BookMetadataProposal,
    sources: list[dict[str, Any]],
    source_values: list[str],
) -> None:
    """Require every proposed metadata field to be supported by supplied evidence."""
    for evidence in proposal.evidence:
        if evidence.source == agents.schemas.EvidenceSource.PAGE_MARKDOWN:
            matching_sources = [source for source in sources if source["page_number"] == evidence.page_number]
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


def resolve_metadata(
    proposal: agents.schemas.BookMetadataProposal | None,
    source_metadata: dict[str, Any],
    sources: list[dict[str, Any]],
    *,
    proposal_validated: bool,
) -> dict[str, Any]:
    """Select accepted model metadata or conservative source metadata for publication."""
    filename = source_metadata.get("filename")
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
    return {
        "title": proposal.title if confident else _fallback_title(source_metadata, sources),
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
