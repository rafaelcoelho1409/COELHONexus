"""Validation and prompt-payload construction for page audits."""
from __future__ import annotations
from . import params
from ..inventory import patterns as inventory_patterns
from .... import agents, graphs

import json
import hashlib
import unicodedata
from json import JSONDecodeError, JSONDecoder
from collections.abc import Iterator
from typing import Any
from pydantic import ValidationError


class ModelCallLimitReachedError(ValueError):
    """The page auditor stopped at its configured model-call limit."""


def extract_assessment_response(
    result: Any,
) -> agents.schemas.PageAuditResponse | agents.schemas.PageAssessment:
    """Validate structured auditor output or recover a JSON response from its last message."""
    structured = result.get("structured_response") if isinstance(result, dict) else None
    if structured is not None:
        return _validate_assessment_response(structured)

    messages = result.get("messages") if isinstance(result, dict) else None
    content = _last_assistant_content(messages)
    if content is None:
        raise ValueError("page auditor returned no structured response or assistant message")
    if "model call limit" in content.casefold():
        raise ModelCallLimitReachedError("page auditor reached its model-call limit")

    last_error: ValidationError | None = None
    for value in _json_objects(content):
        try:
            return _validate_assessment_response(value)
        except ValidationError as error:
            last_error = error
    if last_error is not None:
        raise ValueError("page auditor returned JSON that did not match its response schema") from last_error
    raise ValueError("page auditor returned no structured response and no JSON object")


def _validate_assessment_response(
    value: Any,
) -> agents.schemas.PageAuditResponse | agents.schemas.PageAssessment:
    try:
        return agents.schemas.PageAuditResponse.model_validate(value)
    except ValidationError:
        return agents.schemas.PageAssessment.model_validate(value)


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


def validate_request(
    state: graphs.ingestion.state.PageAuditState,
) -> tuple[str, str, int, str, int]:
    book_id = state.get("book_id")
    extraction_run_id = state.get("extraction_run_id")
    extraction_status = state.get("extraction_status")
    page_ref = state.get("page_ref")

    if not _is_safe_segment(book_id):
        raise ValueError("book_id must be a safe path segment")
    if not _is_safe_segment(extraction_run_id):
        raise ValueError("extraction_run_id must be a safe path segment")
    if extraction_status != "done":
        raise ValueError("only completed extraction runs can be audited")
    if not isinstance(page_ref, dict):
        raise ValueError("page_ref must be a manifest page object")

    page_number = page_ref.get("page")
    object_key = page_ref.get("key")
    pictures = page_ref.get("pictures", 0)
    if type(page_number) is not int or page_number <= 0:
        raise ValueError("page number must be a positive integer")
    if not isinstance(object_key, str) or not object_key:
        raise ValueError("page reference has no object key")
    if type(pictures) is not int or pictures < 0:
        raise ValueError("page reference has an invalid pictures count")

    expected_key = (
        f"bd/books/{book_id}/ingestion/raw/{extraction_run_id}/pages/"
        f"{page_number:04d}.md"
    )
    if object_key != expected_key:
        raise ValueError("page reference does not use its canonical raw object key")

    return book_id, extraction_run_id, page_number, object_key, pictures


def user_payload(page_number: int, pictures: int, markdown: str) -> str:
    return json.dumps({
        "task": "Assess extraction quality for this one physical page.",
        "page_number": page_number,
        "extracted_picture_count": pictures,
        "markdown": markdown,
    }, ensure_ascii = False)


def validate_assessment(
    value: Any,
    *,
    page_number: int,
    pictures: int,
    markdown: str,
) -> agents.schemas.PageAssessment:
    if isinstance(value, agents.schemas.PageAssessment):
        value = value.model_dump(mode = "python")
    assessment = agents.schemas.PageAssessment.model_validate(value)

    if assessment.page_number != page_number:
        raise ValueError("assessment page_number does not match the requested page")
    if len(assessment.issues) > params.MAX_ISSUES_PER_PAGE:
        raise ValueError("assessment exceeds the maximum issues per page")

    media_kinds = {agents.schemas.PageKind.IMAGE_ONLY, agents.schemas.PageKind.MIXED}
    text_kinds = {agents.schemas.PageKind.TEXT, agents.schemas.PageKind.BLANK}
    if pictures > 0 and assessment.page_kind in text_kinds:
        raise ValueError("assessment contradicts the extracted-picture count")
    if pictures == 0 and assessment.page_kind in media_kinds:
        raise ValueError("assessment reports media that the extraction did not find")

    for issue in assessment.issues:
        for evidence in issue.evidence:
            if evidence.source != agents.schemas.EvidenceSource.PAGE_MARKDOWN:
                raise ValueError("page-audit evidence must come from the supplied Markdown")
            if evidence.page_number != page_number:
                raise ValueError("evidence page_number does not match the requested page")
            if len(evidence.excerpt) > params.MAX_EVIDENCE_CHARS:
                raise ValueError("evidence excerpt exceeds the maximum length")
            if evidence.excerpt not in markdown:
                raise ValueError("evidence excerpt is not an exact substring of the source page")

    return assessment


def sanitize_assessment(
    value: Any,
    *,
    page_number: int,
    pictures: int,
    markdown: str,
) -> tuple[agents.schemas.PageAssessment, list[str]]:
    """Keep only auditor findings whose evidence can be verified against this page."""
    if isinstance(value, agents.schemas.PageAuditResponse):
        response = value
    elif isinstance(value, dict) and "page_number" not in value:
        response = agents.schemas.PageAuditResponse.model_validate(value)
    else:
        response = None
    if response is not None:
        assessment = _materialize_assessment(response, page_number)
    else:
        assessment = agents.schemas.PageAssessment.model_validate(value)
    if assessment.page_number != page_number:
        raise ValueError("assessment page_number does not match the requested page")
    if len(assessment.issues) > params.MAX_ISSUES_PER_PAGE:
        raise ValueError("assessment exceeds the maximum issues per page")

    media_kinds = {agents.schemas.PageKind.IMAGE_ONLY, agents.schemas.PageKind.MIXED}
    text_kinds = {agents.schemas.PageKind.TEXT, agents.schemas.PageKind.BLANK}
    warnings = []
    if pictures > 0 and assessment.page_kind in text_kinds:
        warnings.append("The auditor page-kind label conflicted with extracted media; the deterministic label was kept.")
    if pictures == 0 and assessment.page_kind in media_kinds:
        warnings.append("The auditor reported media that extraction did not find; the deterministic label was kept.")

    valid_issues = []
    invalid_evidence_count = 0
    invalid_code_evidence_count = 0
    dropped_issue_count = 0
    self_dismissed_count = 0
    for issue in assessment.issues:
        if inventory_patterns.SELF_DISMISSED_RE.search(issue.description):
            self_dismissed_count += 1
            continue
        source_verified_evidence = [
            evidence
            for evidence in issue.evidence
            if evidence.source == agents.schemas.EvidenceSource.PAGE_MARKDOWN
            and evidence.page_number == page_number
            and len(evidence.excerpt) <= params.MAX_EVIDENCE_CHARS
            and evidence.excerpt in markdown
        ]
        invalid_evidence_count += len(issue.evidence) - len(source_verified_evidence)
        verified_evidence = [
            evidence
            for evidence in source_verified_evidence
            if _evidence_supports_issue_code(issue.code, evidence.excerpt)
        ]
        invalid_code_evidence_count += len(source_verified_evidence) - len(verified_evidence)
        if not verified_evidence:
            dropped_issue_count += 1
            continue
        valid_issues.append(issue.model_copy(update = {"evidence": verified_evidence}))

    if invalid_evidence_count:
        warnings.append(
            f"Discarded {invalid_evidence_count} auditor evidence excerpt(s) that were not exact source text."
        )
    if invalid_code_evidence_count:
        warnings.append(
            f"Discarded {invalid_code_evidence_count} auditor evidence excerpt(s) "
            "that did not support the reported issue code."
        )
    if dropped_issue_count:
        warnings.append(
            f"Discarded {dropped_issue_count} auditor finding(s) without evidence that supports the issue code."
        )
    if self_dismissed_count:
        warnings.append(
            f"Discarded {self_dismissed_count} auditor finding(s) that the auditor itself described as normal."
        )

    return assessment.model_copy(update = {"issues": valid_issues}), warnings


def _content_lines(excerpt: str) -> list[str]:
    """Lines of an excerpt that are neither a picture marker, blank, nor a bare page number."""
    remainder = inventory_patterns.IMAGE_PLACEHOLDER_RE.sub("", excerpt)
    return [
        line for line in remainder.splitlines()
        if line.strip() and not inventory_patterns.PAGE_NUMBER_ONLY_RE.fullmatch(line)
    ]


def _has_page_content(excerpt: str) -> bool:
    """A picture marker or a bare page number is page furniture, never evidence of an extraction defect."""
    return bool(_content_lines(excerpt))


def _evidence_supports_issue_code(issue_code: agents.schemas.IssueCode, excerpt: str) -> bool:
    if issue_code == agents.schemas.IssueCode.CONTROL_CHARACTERS:
        return any(
            unicodedata.category(character) == "Cc" and character not in "\t\n\r"
            for character in excerpt
        )
    if issue_code == agents.schemas.IssueCode.SOFT_HYPHEN:
        return "\u00ad" in excerpt
    if issue_code == agents.schemas.IssueCode.TOC_NOISE:
        return bool(inventory_patterns.TOC_LEADER_ROW_RE.search(excerpt))
    if issue_code == agents.schemas.IssueCode.METADATA:
        # Book metadata has its own stage; a page cannot evidence a defect that no repair specialist is routed.
        return False
    if issue_code == agents.schemas.IssueCode.NEAR_EMPTY:
        # Sparseness is measured in code (inventory); a model calling a title or part page "empty" only adds noise.
        return False
    lines = _content_lines(excerpt)
    if issue_code == agents.schemas.IssueCode.OTHER and inventory_patterns.HTML_ENTITY_RE.search(excerpt):
        # `&amp;` is how Markdown writes an ampersand and renders as one; models still file it, then call it "not a finding".
        return False
    if issue_code in {agents.schemas.IssueCode.STRUCTURE, agents.schemas.IssueCode.TABLE_OR_INDEX_LAYOUT}:
        # Models report "a figure with a caption" as a layout finding; a caption line alone is normal Docling output.
        return not all(inventory_patterns.CAPTION_LINE_RE.match(line) for line in lines)
    return bool(lines)


def _materialize_assessment(
    response: agents.schemas.PageAuditResponse,
    page_number: int,
) -> agents.schemas.PageAssessment:
    issues = []
    seen_ids: dict[str, int] = {}
    for finding in response.findings:
        evidence = [agents.schemas.EvidenceRef(
            source = agents.schemas.EvidenceSource.PAGE_MARKDOWN,
            excerpt = excerpt,
            page_number = page_number,
        ) for excerpt in finding.evidence_excerpts]
        signature = "\0".join((finding.code.value, finding.description, *finding.evidence_excerpts))
        digest = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:16]
        base_id = f"{finding.code.value}:{digest}"
        ordinal = seen_ids.get(base_id, 0) + 1
        seen_ids[base_id] = ordinal
        issue_id = base_id if ordinal == 1 else f"{base_id}:{ordinal}"
        issues.append(agents.schemas.PageIssue(
            issue_id = issue_id,
            code = finding.code,
            severity = finding.severity,
            description = finding.description,
            confidence = finding.confidence,
            evidence = evidence,
        ))
    return agents.schemas.PageAssessment(
        page_number = page_number,
        page_kind = response.page_kind,
        confidence = response.confidence,
        issues = issues,
    )


def _is_safe_segment(value: object) -> bool:
    return isinstance(value, str) and bool(value) and all(
        character.isascii() and (character.isalnum() or character in "-_")
        for character in value
    )
