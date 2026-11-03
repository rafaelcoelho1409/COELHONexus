"""Pure validation and safe application rules for specialist patch proposals."""
from __future__ import annotations
import domains
from .... import agents

import unicodedata
from typing import Any


def validate_proposals(
    value: Any,
    *,
    page_number: int,
    source_sha256: str,
    markdown: str,
    assigned_issues: dict[str, agents.schemas.PageIssue],
    role: agents.schemas.SpecialistRole,
) -> list[agents.schemas.PatchProposal]:
    if isinstance(value, agents.schemas.PatchProposalBatch):
        batch = value
    else:
        batch = agents.schemas.PatchProposalBatch.model_validate(value)

    proposals: list[agents.schemas.PatchProposal] = []
    seen_issue_ids: set[str] = set()
    for proposal in batch.proposals:
        if proposal.page_number != page_number:
            raise ValueError("patch proposal page does not match its source page")
        if proposal.source_sha256 != source_sha256:
            raise ValueError("patch proposal was generated for a different source version")
        if proposal.issue_id not in assigned_issues:
            raise ValueError("patch proposal references an issue not assigned to this specialist")
        if proposal.issue_id in seen_issue_ids:
            raise ValueError("a specialist may propose at most one patch per assigned issue")
        if proposal.issue_code != assigned_issues[proposal.issue_id].code:
            raise ValueError("patch proposal issue code does not match its assigned issue")
        allowed = (
            proposal.issue_code in {
                agents.schemas.IssueCode.CONTROL_CHARACTERS,
                agents.schemas.IssueCode.SPLIT_WORD,
                agents.schemas.IssueCode.SOFT_HYPHEN,
                agents.schemas.IssueCode.FUSED_MARKER,
                agents.schemas.IssueCode.TEXT_CORRUPTION,
            }
            if role == agents.schemas.SpecialistRole.TEXT_REPAIR
            else proposal.issue_code in {
                agents.schemas.IssueCode.STRUCTURE,
                agents.schemas.IssueCode.TOC_NOISE,
                agents.schemas.IssueCode.TABLE_OR_INDEX_LAYOUT,
            }
        )
        if not allowed:
            raise ValueError("patch proposal issue code is outside this specialist's scope")
        if not proposal.target_text or proposal.target_text not in markdown:
            raise ValueError("patch proposal target is not present in the source page")
        start = markdown.find(proposal.target_text)
        if markdown.find(proposal.target_text, start + 1) >= 0:
            raise ValueError("patch proposal target is ambiguous in the source page")
        end = start + len(proposal.target_text)
        if proposal.context_before and markdown[:start].endswith(proposal.context_before) is False:
            raise ValueError("patch proposal context_before does not match the source page")
        if proposal.context_after and not markdown[end:].startswith(proposal.context_after):
            raise ValueError("patch proposal context_after does not match the source page")
        for evidence in proposal.evidence:
            if evidence.source != agents.schemas.EvidenceSource.PAGE_MARKDOWN:
                raise ValueError("patch evidence must come from the supplied Markdown page")
            if evidence.page_number != page_number or evidence.excerpt not in markdown:
                raise ValueError("patch evidence is not an exact excerpt from the supplied page")
        seen_issue_ids.add(proposal.issue_id)
        proposals.append(proposal)

    return proposals


def apply_proposals(
    markdown: str,
    proposals: list[agents.schemas.PatchProposal],
) -> tuple[str, list[str], list[str]]:
    spans = []
    for proposal in proposals:
        start = markdown.find(proposal.target_text)
        spans.append((start, start + len(proposal.target_text), proposal))
    spans.sort(key = lambda span: span[0])
    if any(current[0] < previous[1] for previous, current in zip(spans, spans[1:])):
        raise ValueError("patch proposals contain overlapping source spans")

    normalized = markdown
    applied_issue_ids = []
    review_issue_ids = []
    for start, end, proposal in reversed(spans):
        auto_apply = (
            proposal.risk == agents.schemas.PatchRisk.LOW
            and proposal.confidence >= domains.bd.ingestion.ai.params.AUTO_APPLY_CONFIDENCE
            and _is_mechanical_repair(proposal)
        )
        if auto_apply:
            normalized = normalized[:start] + proposal.replacement + normalized[end:]
            applied_issue_ids.append(proposal.issue_id)
        else:
            review_issue_ids.append(proposal.issue_id)
    return normalized, list(reversed(applied_issue_ids)), list(reversed(review_issue_ids))


def _is_mechanical_repair(proposal: agents.schemas.PatchProposal) -> bool:
    if proposal.issue_code == agents.schemas.IssueCode.SOFT_HYPHEN:
        return "\u00ad" in proposal.target_text and proposal.replacement == proposal.target_text.replace("\u00ad", "")
    if proposal.issue_code == agents.schemas.IssueCode.CONTROL_CHARACTERS:
        expected = "".join(
            character
            for character in proposal.target_text
            if unicodedata.category(character) != "Cc" or character in "\t\n\r"
        )
        return expected != proposal.target_text and proposal.replacement == expected
    return False
