"""Offline replay of the workflow 1.10 run (The Art of Cyberwarfare) through the current repair gates.

The fixture holds, for the 64 pages that had specialist proposals, the source Markdown, the audit issues and the stored
proposals, with what 1.10 applied and sent to review. Run it after any change to the gates, prompts' output contract or
tools: the model is not called, so only the code that decides what reaches a page is measured.

    cd apps/server && .venv/bin/python ../../scripts/server/test_bd_replay_eval.py

Regenerate the fixture from MinIO with scripts/server/export_bd_replay_fixture.py.
"""
import gzip
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "server"))

import domains
import domains.bd.ingestion.ai.params
from ai.bd.agents import schemas
from ai.bd.nodes.ingestion.plan_repairs.domain import STRUCTURE_ISSUES, TEXT_ISSUES
from ai.bd.nodes.ingestion.repair_pages.domain import apply_proposals, validate_proposals
from domains.bd.ingestion.ai.inventory.domain import build_repair_context

FIXTURE = Path(__file__).parent / "data" / "bd_replay_workflow_1_10.json.gz"

# Pages whose 1.10 suggestion was wrong and was correctly kept out of the page (see the 1.10 assessment).
REFUSED_BY_PAGE = {15: "ACKNOWL EDGMENT S", 155: "## 6", 173: "custom nationstate", 259: "VirusTotal -Intelligence"}
# Changed on purpose in 1.11: a soft hyphen must not become a visible hyphen.
NOW_IN_REVIEW = {37: "cyber­ attacks"}


def check(label: str, got, want) -> None:
    assert got == want, f"{label}: expected {want!r}, got {got!r}"
    print(f"ok  {label}")


def replay(page: dict) -> tuple[set[str], set[str], set[str]]:
    """Return (proposal ids, ids the gates accept, ids applied to the page) for one stored page."""
    params = domains.bd.ingestion.ai.params
    markdown = page["source"]
    source_sha256 = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
    issues = [schemas.PageIssue.model_validate(issue) for issue in page["issues"]]
    proposals = [schemas.PatchProposal.model_validate(proposal) for proposal in page["proposals"]]
    accepted: set[str] = set()
    applied: set[str] = set()
    for role, codes in (
        (schemas.SpecialistRole.TEXT_REPAIR, TEXT_ISSUES),
        (schemas.SpecialistRole.STRUCTURE, STRUCTURE_ISSUES),
    ):
        role_proposals = [proposal for proposal in proposals if proposal.issue_code in codes]
        if not role_proposals:
            continue
        assigned = {issue.issue_id: issue for issue in issues if issue.code in codes}
        context = build_repair_context(
            markdown,
            list(assigned.values()),
            per_issue_chars = params.REPAIR_CONTEXT_CHARS_PER_ISSUE,
            max_chars = params.MAX_REPAIR_CONTEXT_CHARS,
        )
        valid = validate_proposals(
            schemas.PatchProposalBatch(proposals = role_proposals),
            page_number = page["page"],
            source_sha256 = source_sha256,
            markdown = markdown,
            assigned_issues = assigned,
            role = role,
            allowed_context = context,
        )
        accepted |= {proposal.issue_id for proposal in valid}
        applied |= set(apply_proposals(markdown, valid)[1])
    return {proposal.issue_id for proposal in proposals}, accepted, applied


with gzip.open(FIXTURE, "rt", encoding = "utf-8") as handle:
    fixture = json.load(handle)

stored_applied_total = replayed_applied_total = proposal_total = 0
newly_applied: list[tuple[int, str]] = []
dropped: dict[int, list[str]] = {}
for page in fixture["pages"]:
    proposal_ids, accepted, applied = replay(page)
    stored_applied = proposal_ids & set(page["auto_applied"])
    proposal_total += len(proposal_ids)
    stored_applied_total += len(stored_applied)
    replayed_applied_total += len(applied)
    newly_applied += [(page["page"], issue_id) for issue_id in sorted(applied - stored_applied)]
    if stored_applied - applied:
        dropped[page["page"]] = sorted(stored_applied - applied)
    check(f"page {page['page']}: every stored proposal still passes validation", accepted, proposal_ids)

check("no proposal is applied that 1.10 did not apply", newly_applied, [])
check("the only repair 1.10 applied that the current gates do not is the soft-hyphen one", sorted(dropped), sorted(NOW_IN_REVIEW))
check("proposals replayed", proposal_total, 112)
check("1.10 applied", stored_applied_total, 99)
check("the current gates apply", replayed_applied_total, 98)

pages = {page["page"]: page for page in fixture["pages"]}
for number, fragment in REFUSED_BY_PAGE.items():
    page = pages[number]
    refused = [
        proposal for proposal in page["proposals"]
        if fragment in proposal["target_text"] and proposal["issue_id"] not in page["auto_applied"]
    ]
    check(f"page {number}: the wrong suggestion {fragment!r} is still kept out of the page", len(refused) >= 1, True)
    _, _, applied = replay(page)
    check(f"page {number}: replay does not apply it", all(proposal["issue_id"] not in applied for proposal in refused), True)

for number, fragment in NOW_IN_REVIEW.items():
    page = pages[number]
    moved = [proposal["issue_id"] for proposal in page["proposals"] if fragment in proposal["target_text"]]
    _, _, applied = replay(page)
    check(f"page {number}: the soft hyphen stays soft (review, not a visible hyphen)", [i for i in moved if i in applied], [])

print("replay eval passed")
