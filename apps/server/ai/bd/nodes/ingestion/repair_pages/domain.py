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
    allowed_context: str | None = None,
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
        if allowed_context is not None and proposal.target_text not in allowed_context:
            raise ValueError("patch proposal target is outside the supplied repair context")
        start = markdown.find(proposal.target_text)
        if markdown.find(proposal.target_text, start + 1) >= 0:
            raise ValueError("patch proposal target is ambiguous in the source page")
        end = start + len(proposal.target_text)
        # The target is exact and unique, so the patch is fully determined; quoted anchors only disambiguate and
        # models often paraphrase them. A wrong anchor is dropped instead of discarding an otherwise valid patch.
        if proposal.context_before and not markdown[:start].endswith(proposal.context_before):
            proposal = proposal.model_copy(update = {"context_before": ""})
        if proposal.context_after and not markdown[end:].startswith(proposal.context_after):
            proposal = proposal.model_copy(update = {"context_after": ""})
        if allowed_context is not None:
            anchored_text = f"{proposal.context_before}{proposal.target_text}{proposal.context_after}"
            if anchored_text not in allowed_context:
                raise ValueError("patch proposal anchor is outside the supplied repair context")
        for evidence in proposal.evidence:
            if evidence.source != agents.schemas.EvidenceSource.PAGE_MARKDOWN:
                raise ValueError("patch evidence must come from the supplied Markdown page")
            if evidence.page_number != page_number or evidence.excerpt not in markdown:
                raise ValueError("patch evidence is not an exact excerpt from the supplied page")
        seen_issue_ids.add(proposal.issue_id)
        proposals.append(proposal)

    return proposals


def build_repair_context(
    markdown: str,
    issues: list[agents.schemas.PageIssue],
    *,
    per_issue_chars: int,
    max_chars: int,
) -> str:
    """Return source-exact windows around issue evidence, bounded before model invocation."""
    windows: list[tuple[int, int]] = []
    for issue in issues:
        for evidence in issue.evidence:
            evidence_start = markdown.find(evidence.excerpt)
            if evidence_start < 0:
                continue
            evidence_end = evidence_start + len(evidence.excerpt)
            budget = max(per_issue_chars, len(evidence.excerpt))
            radius = max(0, (budget - len(evidence.excerpt)) // 2)
            start = max(0, evidence_start - radius)
            end = min(len(markdown), evidence_end + radius)
            windows.append((start, end))

    if not windows:
        return ""

    merged: list[list[int]] = []
    for start, end in sorted(windows):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])

    excerpts: list[str] = []
    remaining = max_chars
    for start, end in merged:
        if remaining <= 0:
            break
        excerpt = markdown[start:min(end, start + remaining)]
        if excerpt:
            excerpts.append(excerpt)
            remaining -= len(excerpt)
    return "\n…\n".join(excerpts)


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


def apply_mechanical_fixes(markdown: str) -> tuple[str, dict[str, int]]:
    """Remove soft hyphens and non-whitespace control characters, which are invisible noise and never content."""
    control_characters = sum(_is_control_noise(character) for character in markdown)
    soft_hyphens = markdown.count("­")
    if not control_characters and not soft_hyphens:
        return markdown, {}
    cleaned = "".join(
        character for character in markdown
        if character != "­" and not _is_control_noise(character)
    )
    counts = {
        agents.schemas.IssueCode.CONTROL_CHARACTERS.value: control_characters,
        agents.schemas.IssueCode.SOFT_HYPHEN.value: soft_hyphens,
    }
    return cleaned, {code: count for code, count in counts.items() if count}


def _is_control_noise(character: str) -> bool:
    return unicodedata.category(character) == "Cc" and character not in "\t\n\r"


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
