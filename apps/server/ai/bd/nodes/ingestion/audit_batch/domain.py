"""Validation and prompt-payload construction for page audits, and the second reader's checks of their findings."""
from __future__ import annotations
import domains
from . import errors, params
from .... import agents

import json
import hashlib
import unicodedata
from collections.abc import Iterator
from typing import Any
from pydantic import ValidationError


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
        raise errors.ModelCallLimitReachedError("page auditor reached its model-call limit")

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
    decoder = json.JSONDecoder()
    for start, character in enumerate(content):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(content[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            yield value


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
    declared_normal_count = 0
    if response is not None:
        defects = [
            finding for finding in response.findings
            if finding.verdict == agents.schemas.FindingVerdict.DEFECT
        ]
        declared_normal_count = len(response.findings) - len(defects)
        assessment = _materialize_assessment(response.model_copy(update = {"findings": defects}), page_number)
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
    self_dismissed_count = declared_normal_count
    for issue in assessment.issues:
        if domains.bd.ingestion.ai.inventory.patterns.SELF_DISMISSED_RE.search(issue.description) or (
            issue.code in (agents.schemas.IssueCode.STRUCTURE, agents.schemas.IssueCode.OTHER)
            and domains.bd.ingestion.ai.inventory.patterns.IMAGE_PLACEMENT_RE.search(issue.description)
        ):
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
    patterns = domains.bd.ingestion.ai.inventory.patterns
    remainder = patterns.IMAGE_PLACEHOLDER_RE.sub("", excerpt)
    return [
        line for line in remainder.splitlines()
        if line.strip() and not patterns.PAGE_NUMBER_ONLY_RE.fullmatch(line)
    ]


def _evidence_supports_issue_code(issue_code: agents.schemas.IssueCode, excerpt: str) -> bool:
    if issue_code == agents.schemas.IssueCode.CONTROL_CHARACTERS:
        return any(
            unicodedata.category(character) == "Cc" and character not in "\t\n\r"
            for character in excerpt
        )
    if issue_code == agents.schemas.IssueCode.SOFT_HYPHEN:
        return "\u00ad" in excerpt
    if issue_code == agents.schemas.IssueCode.TOC_NOISE:
        return bool(domains.bd.ingestion.ai.inventory.patterns.TOC_LEADER_ROW_RE.search(excerpt))
    if issue_code == agents.schemas.IssueCode.METADATA:
        # Book metadata has its own stage; a page cannot evidence a defect that no repair specialist is routed.
        return False
    if issue_code == agents.schemas.IssueCode.NEAR_EMPTY:
        # Sparseness is measured in code (inventory); a model calling a title or part page "empty" only adds noise.
        return False
    lines = _content_lines(excerpt)
    if issue_code == agents.schemas.IssueCode.OTHER and (
        domains.bd.ingestion.ai.inventory.patterns.HTML_ENTITY_RE.search(excerpt)
    ):
        # `&amp;` is how Markdown writes an ampersand and renders as one; models still file it, then call it "not a finding".
        return False
    if issue_code in {agents.schemas.IssueCode.STRUCTURE, agents.schemas.IssueCode.TABLE_OR_INDEX_LAYOUT}:
        # Models report "a figure with a caption" as a layout finding; a caption line alone is normal Docling output.
        return not all(domains.bd.ingestion.ai.inventory.patterns.CAPTION_LINE_RE.match(line) for line in lines)
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
            code = agents.schemas.IssueCode(finding.code.value),
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


def select_candidates(issues: list[agents.schemas.PageIssue]) -> list[agents.schemas.PageIssue]:
    """The model-reported findings that are judgement calls: a vague code, or a confidence below the bar.

    Findings that name a concrete character-level defect (a split word, a control character) are checked against the
    page by code already and are not worth a second call."""
    limits = domains.bd.ingestion.ai.params
    return [
        issue for issue in issues
        if issue.code.value in limits.VERIFIED_CODES or issue.confidence < limits.VERIFY_BELOW_CONFIDENCE
    ]


def build_verification_payload(page_number: int, candidates: list[agents.schemas.PageIssue], markdown: str) -> str:
    limits = domains.bd.ingestion.ai.params
    return json.dumps({
        "task": "Decide whether each finding is a real extraction defect shown by the page text.",
        "page_number": page_number,
        "findings": [{
            "issue_id": issue.issue_id,
            "code": issue.code.value,
            "description": issue.description,
            "evidence_excerpts": [evidence.excerpt for evidence in issue.evidence],
        } for issue in candidates],
        "page_excerpts": domains.bd.ingestion.ai.inventory.domain.build_repair_context(
            markdown,
            candidates,
            per_issue_chars = limits.REPAIR_CONTEXT_CHARS_PER_ISSUE,
            max_chars = limits.MAX_VERIFIER_CONTEXT_CHARS,
        ),
    }, ensure_ascii = False)


def extract_checks(result: Any) -> agents.schemas.FindingCheckBatch:
    structured = result.get("structured_response") if isinstance(result, dict) else None
    if structured is None:
        raise ValueError("the finding verifier returned no structured response")
    try:
        return agents.schemas.FindingCheckBatch.model_validate(structured)
    except ValidationError as error:
        raise ValueError("the finding verifier returned a response that did not match its schema") from error


def apply_checks(
    issues: list[agents.schemas.PageIssue],
    candidates: list[agents.schemas.PageIssue],
    batch: agents.schemas.FindingCheckBatch,
) -> tuple[list[agents.schemas.PageIssue], list[str]]:
    """Drop only the candidates the verifier clearly dismissed; anything it skipped or does not name stays."""
    shown = {issue.issue_id for issue in candidates}
    dismissed = {
        check.issue_id for check in batch.checks
        if check.issue_id in shown and check.verdict == agents.schemas.FindingVerdict.NOT_A_DEFECT
    }
    kept = [issue for issue in issues if issue.issue_id not in dismissed]
    if not dismissed:
        return kept, []
    return kept, [
        f"Discarded {len(dismissed)} auditor finding(s) that a second reader found to be normal page content."
    ]

