"""Offline end-to-end check of the Book Distiller agentic ingestion workflow.

    cd apps/server && .venv/bin/python ../../scripts/server/test_bd_ingestion_full_workflow.py
"""
import asyncio
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "server"))

from ai.bd.agents import schemas
from ai.bd.nodes.ingestion.metadata_normalization.service import _fallback_title, _matches_filename_label
from ai.bd.nodes.ingestion.repair_pages.domain import (
    apply_mechanical_fixes,
    apply_mechanical_fixes_for_issues,
    apply_proposals,
)
from ai.bd.nodes.ingestion.repair_pages.domain import validate_proposals
from ai.bd.nodes.ingestion.audit_batch.domain import sanitize_assessment
from domains.bd.ingestion.ai.inventory.domain import assess_page, build_repair_context, effective_length, merge_issues
import domains
import infra
from domains.bd.ingestion.ai.service import (
    create_ingestion_ai_run,
    mark_ingestion_ai_run_failed,
    queue_ingestion_ai_resume,
    read_display_page,
    read_ingestion_ai_run,
    read_page_quality,
    run_ingestion_ai,
)
from domains.bd.ingestion.storage import keys


async def no_progress(*_args, **_kwargs):
    return None


domains.bd.ingestion.ai.progress.service.emit = no_progress
domains.settings.llm_counter.service.snapshot = no_progress
domains.bd.ingestion.ai.progress.service.clear_cancel = no_progress
domains.bd.ingestion.ai.progress.service.clear_events = no_progress


class FakeLock:
    async def __aenter__(self):
        return True

    async def __aexit__(self, *_args):
        return False


class FakeRedis:
    def lock(self, *_args, **_kwargs):
        return FakeLock()


@asynccontextmanager
async def fake_redis_client():
    yield FakeRedis()


infra.redis.service.client = fake_redis_client


class FakeStorage:
    def __init__(self, manifest: dict, pages: dict[str, str]):
        self.values = {keys.manifest_key(manifest["book_id"]): json.dumps(manifest)}
        self.values.update(pages)
        self.reads: list[str] = []
        self.writes: list[tuple[str, str, str]] = []
        self.fail_once_key: str | None = None

    async def read_text(self, key: str) -> str:
        self.reads.append(key)
        try:
            return self.values[key]
        except KeyError as error:
            raise FileNotFoundError(key) from error

    async def write(self, key: str, value: str, content_type: str) -> None:
        if key == self.fail_once_key:
            self.fail_once_key = None
            raise RuntimeError("simulated transient object-store failure")
        self.writes.append((key, value, content_type))
        self.values[key] = value

    async def exists(self, key: str) -> bool:
        return key in self.values

    async def list_subfolders(self, prefix: str) -> list[str]:
        return sorted({
            key[len(prefix):].split("/", 1)[0]
            for key in self.values
            if key.startswith(prefix) and key[len(prefix):]
        })


class FakeAuditor:
    def __init__(self):
        self.calls = 0

    async def ainvoke(self, payload: dict, config: dict | None = None) -> dict:
        self.calls += 1
        request = json.loads(payload["messages"][0]["content"])
        page_number = request["page_number"]
        text = request["markdown"].strip()
        kind = schemas.PageKind.TEXT if text else schemas.PageKind.BLANK
        return {"structured_response": schemas.PageAssessment(
            page_number = page_number,
            page_kind = kind,
            confidence = 0.99,
            issues = [],
        )}


class FakeTextRepair:
    def __init__(self):
        self.calls = 0

    async def ainvoke(self, payload: dict, config: dict | None = None, context = None) -> dict:
        self.calls += 1
        request = json.loads(payload["messages"][0]["content"])
        issue = request["assigned_issues"][0]
        target = "exam\u00adple"
        return {"structured_response": schemas.PatchProposalBatch(proposals=[
            schemas.PatchProposal(
                page_number = request["page_number"],
                issue_id = issue["issue_id"],
                source_sha256 = request["source_sha256"],
                issue_code = schemas.IssueCode.SOFT_HYPHEN,
                risk = schemas.PatchRisk.LOW,
                target_text = target,
                replacement = "example",
                rationale = "Remove a soft hyphen splitting a word.",
                confidence = 0.999,
                evidence=[schemas.EvidenceRef(
                    source = schemas.EvidenceSource.PAGE_MARKDOWN,
                    excerpt = target,
                    page_number = request["page_number"],
                )],
            ),
        ])}


class FakeStructureRepair:
    def __init__(self):
        self.calls = 0

    async def ainvoke(self, payload: dict, config: dict | None = None, context = None) -> dict:
        self.calls += 1
        return {"structured_response": schemas.PatchProposalBatch(proposals=[])}


class FakeMetadataNormalizer:
    def __init__(self):
        self.calls = 0

    async def ainvoke(self, payload: dict, config: dict | None = None) -> dict:
        self.calls += 1
        return {"structured_response": schemas.BookMetadataProposal(
            title = "An Example Book",
            authors = ["A. Author"],
            confidence = 0.999,
            rationale = "The title and author are explicitly shown in front matter.",
            evidence=[schemas.EvidenceRef(
                source = schemas.EvidenceSource.PAGE_MARKDOWN,
                excerpt = "An exam\u00adple book by A. Author",
                page_number = 1,
            )],
        )}


class UnsupportedMetadataNormalizer:
    async def ainvoke(self, payload: dict, config: dict | None = None) -> dict:
        return {"structured_response": schemas.BookMetadataProposal(
            title = "Fabricated Title",
            confidence = 0.999,
            rationale = "This title is not in the source evidence.",
            evidence=[schemas.EvidenceRef(
                source = schemas.EvidenceSource.PAGE_MARKDOWN,
                excerpt = "An exam\u00adple book",
                page_number = 1,
            )],
        )}


class InvalidAuditor:
    async def ainvoke(self, payload: dict, config: dict | None = None) -> dict:
        return {"structured_response": schemas.PageAssessment(
            page_number = 999,
            page_kind = schemas.PageKind.TEXT,
            confidence = 1.0,
            issues = [],
        )}


book_id = "book-full-flow"
extraction_id = "extract-001"
raw_pages = {
    keys.page_key(book_id, 1, extraction_id): "An exam\u00adple book by A. Author\n",
    keys.page_key(book_id, 2, extraction_id): "  \n",
}
manifest = {
    "book_id": book_id,
    "extraction_id": extraction_id,
    "status": "done",
    "n_pages": 2,
    "filename": "an_example_book.epub",
    "pages": [
        {"page": 1, "key": keys.page_key(book_id, 1, extraction_id), "pictures": 0},
        {"page": 2, "key": keys.page_key(book_id, 2, extraction_id), "pictures": 0},
    ],
}
storage = FakeStorage(manifest, raw_pages)
auditor = FakeAuditor()
result = asyncio.run(run_ingestion_ai(
    book_id,
    agentic_run_id = "agent-run-001",
    storage = storage,
    auditor = auditor,
    text_repair = FakeTextRepair(),
    structure_repair = FakeStructureRepair(),
    metadata_normalizer = FakeMetadataNormalizer(),
))
assert result["status"] == "complete"
assert result["quality_status"] == "complete"
assert result["quality_report"]["blank_pages"] == [2]
assert result["quality_report"]["delivered_page_numbers"] == [1]
assert result["quality_report"]["discarded_blank_pages"] == [2]
assert auditor.calls == 1
assert result["quality_report"]["patches_auto_applied"] == 1
assert result["book_metadata"]["title"] == "An Example Book"
assert storage.values[keys.page_key(book_id, 1, extraction_id)] == "An exam\u00adple book by A. Author\n"
assert storage.values[keys.agentic_page_key(book_id, "agent-run-001", 1)] == "An example book by A. Author\n"
assert storage.values[keys.agentic_page_key(book_id, "agent-run-001", 2)] == "  \n"
final_manifest = json.loads(storage.values[keys.agentic_manifest_key(book_id, "agent-run-001")])
assert final_manifest["status"] == "done"
assert len(final_manifest["pages"]) == 2
assert final_manifest["pages"][1]["delivery_disposition"] == "discarded_blank"
assert final_manifest["delivered_page_count"] == 1
assert storage.writes[-1][0] == keys.agentic_manifest_key(book_id, "agent-run-001")
assert [key for key, _, _ in storage.writes[:2]] == [
    keys.agentic_manifest_key(book_id, "agent-run-001"),
    keys.agentic_manifest_key(book_id, "agent-run-001"),
]
print("ok  end-to-end fake-agent run writes versioned artifacts without mutating raw pages")
print("ok  blank pages are deterministically classified without an unnecessary auditor call")
write_count = len(storage.writes)
try:
    asyncio.run(run_ingestion_ai(
        book_id,
        agentic_run_id = "agent-run-001",
        storage = storage,
        auditor = FakeAuditor(),
        text_repair = FakeTextRepair(),
        structure_repair = FakeStructureRepair(),
        metadata_normalizer = FakeMetadataNormalizer(),
    ))
except ValueError:
    assert len(storage.writes) == write_count
else:
    raise AssertionError("completed agentic run IDs must not be overwritten")
print("ok  existing agentic run IDs cannot overwrite immutable output history")

split_word = schemas.PatchProposal(
    page_number = 1,
    issue_id = "split-word-1",
    source_sha256 = "a" * 64,
    issue_code = schemas.IssueCode.SPLIT_WORD,
    risk = schemas.PatchRisk.LOW,
    target_text = "cyber-\nwarfare",
    replacement = "cyberwarfare",
    rationale = "Join the line-broken word.",
    confidence = 0.999,
    evidence=[schemas.EvidenceRef(
        source = schemas.EvidenceSource.PAGE_MARKDOWN,
        excerpt = "cyber-\nwarfare",
        page_number = 1,
    )],
)
unchanged, auto_applied, review_required = apply_proposals("cyber-\nwarfare", [split_word])
assert unchanged == "cyber-\nwarfare" and not auto_applied and review_required == ["split-word-1"]
print("ok  uncertain text reconstruction remains review-only even at high model confidence")

def spacing_proposal(
    target: str,
    replacement: str,
    *,
    confidence: float = 0.95,
    risk = schemas.PatchRisk.LOW,
    code = schemas.IssueCode.SPLIT_WORD,
    evidence: str | None = None,
) -> schemas.PatchProposal:
    return schemas.PatchProposal(
        page_number = 1, issue_id = "spacing-1", source_sha256 = "a" * 64, issue_code = code, risk = risk,
        target_text = target, replacement = replacement, rationale = "Fix the extraction defect.",
        confidence = confidence,
        evidence = [schemas.EvidenceRef(
            source = schemas.EvidenceSource.PAGE_MARKDOWN, excerpt = evidence or target, page_number = 1,
        )],
    )


def applies(page: str, proposal: schemas.PatchProposal) -> bool:
    patched, applied, review = apply_proposals(page, [proposal])
    assert (applied, review) in (([proposal.issue_id], []), ([], [proposal.issue_id]))
    assert (patched != page) == bool(applied)
    return bool(applied)


# Replays of proposals from the 2026-11-05 run: every one only adds or removes spaces.
for target, replacement, code in (
    ("I NTRODUC T I ON", "INTRODUCTION", schemas.IssueCode.SPLIT_WORD),
    ("## ENDNOT ES", "## ENDNOTES", schemas.IssueCode.SPLIT_WORD),
    ("NOT E", "NOTE", schemas.IssueCode.TEXT_CORRUPTION),
    ("DoDaffiliated", "DoD affiliated", schemas.IssueCode.FUSED_MARKER),
    ("https://research .checkpoint.com/a", "https://research.checkpoint.com/a", schemas.IssueCode.SPLIT_WORD),
    ("denial-ofservice", "denial-of-service", schemas.IssueCode.SPLIT_WORD),
    ("spe- cific", "specific", schemas.IssueCode.SPLIT_WORD),
    ("social engineer- ing", "social engineering", schemas.IssueCode.SPLIT_WORD),
    ("trade­ craft", "tradecraft", schemas.IssueCode.SPLIT_WORD),
    (
        "https://www.wired.com/ images\\_blogs/threatlevel/2013/hidden\\_lynx\\_final.pdf",
        "https://www.wired.com/images_blogs/threatlevel/2013/hidden_lynx_final.pdf",
        schemas.IssueCode.SPLIT_WORD,
    ),
    ("cyber­ attacks", "cyberattacks", schemas.IssueCode.SPLIT_WORD),
    ("commandand-control server", "command-and-control server", schemas.IssueCode.SPLIT_WORD),
):
    page = f"Before.\n\n{target}\n\nAfter."
    assert applies(page, spacing_proposal(target, replacement, code = code)), target
print("ok  a repair that only adds or removes spaces (or plain-prose hyphens) is applied without review")

assert applies("Before. I NTRODUC T I ON After.", spacing_proposal(
    "I NTRODUC T I ON", "INTRODUCTION", confidence = 0.9, evidence = "Before. I NTRODUC T I ON After.",
))
for label, page, proposal in (
    ("letters change", "see avail- able", spacing_proposal("avail-", "available")),
    ("a line break is joined", "cyber-\nwarfare", spacing_proposal("cyber-\nwarfare", "cyberwarfare")),
    ("confidence below the spacing floor", "NOT E x", spacing_proposal("NOT E", "NOTE", confidence = 0.7)),
    ("risk is high", "NOT E x", spacing_proposal("NOT E", "NOTE", risk = schemas.PatchRisk.HIGH)),
    ("a space is inserted into a URL path", "see https://x.com/a/offensesrelated-2016-election/ now", spacing_proposal(
        "offensesrelated-2016-election", "offenses related-2016-election",
    )),
    ("a space is inserted into an address", "mail a.b@corp.comreports please", spacing_proposal(
        "a.b@corp.comreports", "a.b@corp.com reports",
    )),
    ("an escaped underscore is dropped inside inline code", "run `curl https://a.com/x\\_y` now", spacing_proposal(
        "x\\_y", "x_y",
    )),
    ("an escaped underscore is dropped inside a code fence", "```\nhttps://a.com/x\\_y\n```", spacing_proposal(
        "x\\_y", "x_y",
    )),
    ("an escape and a letter change together", "see https://a.com/x\\_y now", spacing_proposal(
        "x\\_y", "X_y",
    )),
    ("an existing hyphen is removed", "a state-of-the-art tool", spacing_proposal(
        "state-of-the-art tool", "stateoftheart tool",
    )),
    ("a soft hyphen becomes a visible hyphen", "the cyber­ attacks grew", spacing_proposal(
        "cyber­ attacks", "cyber-attacks",
    )),
    ("a hyphen is dropped without a dangling space", "social engineer-ing", spacing_proposal(
        "social engineer-ing", "social engineering",
    )),
    ("list marker is glued to its text", "- item text", spacing_proposal("- item text", "-itemtext")),
    ("indent is added", "see\n\ncode line", spacing_proposal("code line", "    code line")),
    ("heading marker is glued", "## Title", spacing_proposal("## Title", "##Title")),
    ("digits change a hyphen", "SHA-256 value", spacing_proposal("SHA-256 value", "SHA256 value")),
    ("target is outside the issue evidence", "NOT E and I NTRODUC T I ON", spacing_proposal(
        "NOT E", "NOTE", evidence = "I NTRODUC T I ON",
    )),
    ("structure code", "## A B", spacing_proposal("## A B", "## AB", code = schemas.IssueCode.STRUCTURE)),
):
    assert not applies(page, proposal), label
print("ok  letter, line-break, marker, indent, low-confidence, off-evidence and structure edits stay review-only")

ip_page = "Hosts: 81.169.145.82, 84.246.123.242 and 173.194.121 .36 were seen.\n"
ip_fix = spacing_proposal("173.194.121 .36", "173.194.121.36")
ip_rewrite = schemas.PatchProposal(**{
    **spacing_proposal(
        "81.169.145.82, 84.246.123.242 and 173.194.121 .36",
        "81.169.145.82\n84.246.123.242\n173.194.121.36",
        code = schemas.IssueCode.STRUCTURE,
    ).model_dump(),
    "issue_id": "rewrite-1",
})
patched_ips, applied_ips, reviewed_ips = apply_proposals(ip_page, [ip_rewrite, ip_fix])
assert applied_ips == ["spacing-1"] and reviewed_ips == ["rewrite-1"] and "173.194.121.36" in patched_ips
second_fix = schemas.PatchProposal(**{**ip_fix.model_dump(), "issue_id": "spacing-2"})
_, applied_twice, reviewed_twice = apply_proposals(ip_page, [ip_fix, second_fix])
assert applied_twice == [] and sorted(reviewed_twice) == ["spacing-1", "spacing-2"]
print("ok  a review-only proposal does not block a verified repair inside its span; two competing repairs both wait")

truncated_page = "In the private industry, cyber"
invented = spacing_proposal(
    truncated_page,
    "In the private industry, cybersecurity firms also played a role in detecting attacks.",
    code = schemas.IssueCode.TEXT_CORRUPTION,
)
assert validate_proposals(
    schemas.PatchProposalBatch(proposals = [invented]),
    page_number = 1, source_sha256 = "a" * 64, markdown = truncated_page,
    assigned_issues = {"spacing-1": schemas.PageIssue(
        issue_id = "spacing-1", code = schemas.IssueCode.TEXT_CORRUPTION, severity = schemas.IssueSeverity.HIGH,
        description = "Page ends abruptly.", confidence = 0.9, evidence = invented.evidence,
    )},
    role = schemas.SpecialistRole.TEXT_REPAIR,
) == []
print("ok  a proposal that appends new words to a page that ends mid-sentence is dropped as invented text")


def validated_batch(page: str, *proposals: schemas.PatchProposal) -> list[schemas.PatchProposal]:
    return validate_proposals(
        schemas.PatchProposalBatch(proposals = list(proposals)),
        page_number = 1, source_sha256 = "a" * 64, markdown = page,
        assigned_issues = {proposal.issue_id: schemas.PageIssue(
            issue_id = proposal.issue_id, code = proposal.issue_code, severity = schemas.IssueSeverity.LOW,
            description = "Reported.", confidence = 0.9, evidence = proposal.evidence,
        ) for proposal in proposals},
        role = schemas.SpecialistRole.STRUCTURE
        if proposals[0].issue_code == schemas.IssueCode.STRUCTURE else schemas.SpecialistRole.TEXT_REPAIR,
    )


caption_page = "Text.\n\n<!-- image -->\n\nFigure 6-1: A blog.\n\nMore text.\n"
for label, target, replacement in (
    ("an image link to a made-up path", "<!-- image -->", "![Figure 6-1: A blog.](image_path.png)"),
    ("a made-up page-break marker", "Figure 6-1: A blog.", "<!-- pagebreak -->\n\nFigure 6-1: A blog."),
):
    assert validated_batch(caption_page, spacing_proposal(
        target, replacement, code = schemas.IssueCode.STRUCTURE,
    )) == [], label
real_link_page = "See [the post](https://a .com/x) now."
assert len(validated_batch(real_link_page, spacing_proposal(
    "[the post](https://a .com/x)", "[the post](https://a.com/x)",
))) == 1
print("ok  a replacement may repair a link or marker but never introduce a new image path, link or comment")

two_issue_page = "NOT E one.\n\nL ANDSCAPE and L ANDSCAPE again.\n"
good = spacing_proposal("NOT E", "NOTE", evidence = "NOT E one.")
ambiguous = schemas.PatchProposal(
    **{**spacing_proposal("L ANDSCAPE", "LANDSCAPES").model_dump(), "issue_id": "spacing-2"},
)
assert [proposal.issue_id for proposal in validated_batch(two_issue_page, good, ambiguous)] == ["spacing-1"]
try:
    validated_batch(two_issue_page, ambiguous)
except ValueError:
    pass
else:
    raise AssertionError("a batch with no usable proposal must still be rejected so it can be retried")
print("ok  an ambiguous or absent target costs only its own proposal while the rest of the batch is kept")

repeated = spacing_proposal("L ANDSCAPE", "LANDSCAPE")
assert [proposal.issue_id for proposal in validated_batch(two_issue_page, repeated)] == ["spacing-1"]
patched_twice, applied_everywhere, _ = apply_proposals(two_issue_page, [repeated])
assert applied_everywhere == ["spacing-1"] and patched_twice.count("LANDSCAPE") == 2 and "L ANDSCAPE" not in patched_twice
url_twice = "See https://a.com/xy and again https://a.com/xy."
_, applied_url, reviewed_url = apply_proposals(url_twice, [spacing_proposal("xy", "x y")])
assert applied_url == [] and reviewed_url == ["spacing-1"]
print("ok  a verified space-only repair of a phrase that occurs twice is applied at both places; inside a URL it waits")

soft_hyphen_issue = schemas.PageIssue(
    issue_id = "soft-1", code = schemas.IssueCode.SOFT_HYPHEN, severity = schemas.IssueSeverity.LOW,
    description = "Found 1 soft hyphen(s).", confidence = 1.0,
    evidence = [schemas.EvidenceRef(
        source = schemas.EvidenceSource.PAGE_MARKDOWN, excerpt = "cyber­ attacks", page_number = 1,
    )],
)
_, _, resolved_ids = apply_mechanical_fixes_for_issues("cyberattacks", [soft_hyphen_issue])
assert resolved_ids == ["soft-1"]
print("ok  a soft-hyphen finding is resolved when a space-only repair already swallowed the soft hyphen")

# Workflow 1.13: the gate judges the words an edit changes, not the unchanged words around them (page 221, run 511c2e4a).
assert applies(
    "Before.\n\ncommandand-control domain software-update.org\n\nAfter.",
    spacing_proposal("commandand-control domain software-update.org", "command-and-control domain software-update.org"),
)
for label, page, proposal in (
    ("the edited word holds digits", "see CVE2010-0249 now", spacing_proposal("CVE2010-0249", "CVE-2010-0249")),
    ("a hyphen is added inside a URL", "see https://x.com/abcd now", spacing_proposal(
        "see https://x.com/abcd now", "see https://x.com/ab-cd now",
    )),
    ("a letter changes beside an unchanged domain", "C C&C domain a.org", spacing_proposal(
        "C C&C domain a.org", "C&C domain a.org",
    )),
):
    assert not applies(page, proposal), label
print("ok  a wide target does not hide a plain-prose hyphen repair, and does not let a changed URL, number or letter through")

cleaned_text, cleaned_counts = apply_mechanical_fixes("the cyber­ attacks grew; trade­ Craft; a soft­hyphen")
assert cleaned_text == "the cyberattacks grew; trade Craft; a softhyphen" and cleaned_counts == {"soft_hyphen": 3}, cleaned_text
print("ok  a soft hyphen before a space and a lowercase word is a line break and joins the word; elsewhere it is only removed")

audit_markdown = (
    "Body text.\n\n<!-- image -->\n\nFigure 1: A caption.\n\n17\n\nC&amp;C server.\n\n| a | b |\n\n"
    "Syria, Egypt and . . ., and also Iran.\n\n| Intro . . . . . . 7 |\n"
)


def audit_findings(
    *excerpts: str,
    code = schemas.IssueCode.OTHER,
    description: str = "Reported by the model.",
    verdict = schemas.FindingVerdict.DEFECT,
) -> list[schemas.IssueCode]:
    response = schemas.PageAuditResponse(
        page_kind = schemas.PageKind.MIXED, confidence = 0.9,
        findings = [schemas.PageAuditFinding(
            code = schemas.AuditIssueCode(code.value), severity = schemas.IssueSeverity.LOW, description = description,
            confidence = 0.9, evidence_excerpts = [excerpt], verdict = verdict,
        ) for excerpt in excerpts],
    )
    assessment, _ = sanitize_assessment(response, page_number = 1, pictures = 1, markdown = audit_markdown)
    return [issue.evidence[0].excerpt for issue in assessment.issues]


assert audit_findings("<!-- image -->", "17", "\n17\n", "Figure 1: A caption.") == ["Figure 1: A caption."]
assert audit_findings("<!-- image -->", code = schemas.IssueCode.STRUCTURE) == []
assert audit_findings("Figure 1: A caption.", code = schemas.IssueCode.STRUCTURE) == []
assert audit_findings("Body text.", code = schemas.IssueCode.STRUCTURE) == ["Body text."]
for code_the_auditor_may_not_report in (schemas.IssueCode.METADATA, schemas.IssueCode.NEAR_EMPTY):
    # Sparseness is measured in code and metadata has its own stage, so the response schema does not allow either code.
    try:
        audit_findings("Body text.", code = code_the_auditor_may_not_report)
    except ValueError:
        pass
    else:
        raise AssertionError(f"the auditor schema accepted {code_the_auditor_may_not_report}")
assert audit_findings("Body text.", code = schemas.IssueCode.OTHER, verdict = schemas.FindingVerdict.NOT_A_DEFECT) == []
assert audit_findings("Body text.", code = schemas.IssueCode.OTHER, verdict = schemas.FindingVerdict.DEFECT) == ["Body text."]
assert audit_findings("Figure 1: A caption.", code = schemas.IssueCode.TABLE_OR_INDEX_LAYOUT) == []
assert audit_findings("| a | b |", code = schemas.IssueCode.TABLE_OR_INDEX_LAYOUT) == ["| a | b |"]
assert audit_findings("C&amp;C server.", code = schemas.IssueCode.OTHER) == []
assert audit_findings("Body text.", code = schemas.IssueCode.STRUCTURE, description = "A figure and text, which is typical for a mixed page.") == []
assert audit_findings("Body text.", code = schemas.IssueCode.STRUCTURE, description = "The paragraph is cut in two.") == ["Body text."]
for placement in (
    "The page contains a mix of text and an image, but the image is not properly integrated into the flow of the text.",
    "The page contains a mix of textual content and a single embedded image (Figure 7-4).",
    "The image is not rendered or described in the markdown.",
):
    assert audit_findings("Body text.", code = schemas.IssueCode.STRUCTURE, description = placement) == [], placement
    assert audit_findings("Body text.", code = schemas.IssueCode.OTHER, description = placement) == [], placement
assert audit_findings("Body text.", code = schemas.IssueCode.SPLIT_WORD, description = "A mix of text and an image, a word is split.") == ["Body text."]
assert audit_findings("Syria, Egypt and . . ., and also Iran.", code = schemas.IssueCode.TOC_NOISE) == []
assert audit_findings("| Intro . . . . . . 7 |", code = schemas.IssueCode.TOC_NOISE) == ["| Intro . . . . . . 7 |"]
print("ok  a picture marker, bare page number or lone figure caption is never accepted as evidence of a defect")

assert effective_length("a" * 100) == 100 and effective_length("| a" + " " * 40_000 + "|") == 5
print("ok  table padding does not count against the page size limit")


metadata_storage = FakeStorage(manifest, raw_pages)
metadata_result = asyncio.run(run_ingestion_ai(
    book_id,
    agentic_run_id = "agent-run-002",
    storage = metadata_storage,
    auditor = FakeAuditor(),
    text_repair = FakeTextRepair(),
    structure_repair = FakeStructureRepair(),
    metadata_normalizer = UnsupportedMetadataNormalizer(),
))
assert metadata_result["book_metadata"]["title"] == "an example book"
assert metadata_result["book_metadata"]["review_required"] is True
print("ok  unsupported metadata proposals fall back to a filename-derived title for review")
fallback_title = _fallback_title(
    {"filename": "art-of-cyberwarfare-(source-copy).pdf", "title": "art-of-cyberwarfare-(source-copy).pdf"},
    [{"page_number": 1, "markdown": "# The Art of Cyberwarfare\n\nOther front matter."}],
)
assert fallback_title == "The Art of Cyberwarfare"
print("ok  metadata fallback prefers a clear book-title heading over a filename label")

blank_page = assess_page(3, "<!-- image -->\n", 0)
image_only_page = assess_page(4, "<!-- image -->\n", 1)
number_only_page = assess_page(5, "  17  \n", 0)
heading_page = assess_page(6, "L ANDSCAPE\n\nBody text with enough content to avoid the sparse-page warning.\n", 0)
normal_heading_page = assess_page(8, "A LANDSCAPE OF IDEAS\n", 0)
introduction_heading_page = assess_page(9, "I NTRODUC T I ON\n\nOpening text.\n", 0)
toc_page = assess_page(7, "Chapter One ........ 12\nChapter Two ........ 34\nChapter Three ........ 56\n", 0)
near_empty_page = assess_page(10, "Copyright.\n", 0)
normal_table_page = assess_page(11, "| Name | Pages |\n| --- | --- |\n| Introduction | 1 |\n| Conclusion | 9 |\n", 0)
malformed_table_page = assess_page(12, "| Name | Pages |\n| --- | --- |\n| Introduction | 1 | Extra |\n| Conclusion | 9 |\n", 0)
assert blank_page.page_kind == schemas.PageKind.BLANK
assert image_only_page.page_kind == schemas.PageKind.IMAGE_ONLY
assert number_only_page.page_kind == schemas.PageKind.BLANK
assert [issue.code for issue in heading_page.issues] == [schemas.IssueCode.SPLIT_WORD]
assert not normal_heading_page.issues
assert schemas.IssueCode.SPLIT_WORD in {issue.code for issue in introduction_heading_page.issues}
assert [issue.code for issue in toc_page.issues] == [schemas.IssueCode.TOC_NOISE]
assert [issue.code for issue in near_empty_page.issues] == [schemas.IssueCode.NEAR_EMPTY]
assert not normal_table_page.issues
assert [issue.code for issue in malformed_table_page.issues] == [schemas.IssueCode.TABLE_OR_INDEX_LAYOUT]
print("ok  deterministic checks distinguish blank, image-only, near-empty, split-heading, table, and TOC pages")

source_context = build_repair_context(
    "Before " + "x" * 3000 + " CORRUPT heading " + "y" * 3000 + " after",
    [schemas.PageIssue(
        issue_id = "focus",
        code = schemas.IssueCode.TEXT_CORRUPTION,
        severity = schemas.IssueSeverity.MEDIUM,
        description = "A damaged span needs review.",
        confidence = 1.0,
        evidence=[schemas.EvidenceRef(
            source = schemas.EvidenceSource.PAGE_MARKDOWN,
            excerpt = "CORRUPT heading",
            page_number = 1,
        )],
    )],
    per_issue_chars = 500,
    max_chars = 1000,
)
assert "CORRUPT heading" in source_context and len(source_context) < 1000
assert "x" * 2000 not in source_context and "y" * 2000 not in source_context
print("ok  specialist prompts receive bounded, source-exact issue context")

resume_run_id = "agent-run-resume"
resume_storage = FakeStorage(manifest, raw_pages)
resume_storage.fail_once_key = keys.agentic_quality_report_key(book_id, resume_run_id)
resume_checkpointer = InMemorySaver()
resume_agents = (FakeAuditor(), FakeTextRepair(), FakeStructureRepair(), FakeMetadataNormalizer())
try:
    asyncio.run(run_ingestion_ai(
        book_id,
        agentic_run_id = resume_run_id,
        storage = resume_storage,
        auditor = resume_agents[0],
        text_repair = resume_agents[1],
        structure_repair = resume_agents[2],
        metadata_normalizer = resume_agents[3],
        checkpointer = resume_checkpointer,
    ))
except RuntimeError as error:
    assert "simulated transient" in str(error)
else:
    raise AssertionError("the injected publication failure must interrupt the first run")
failed_manifest = json.loads(resume_storage.values[keys.agentic_manifest_key(book_id, resume_run_id)])
assert failed_manifest["status"] == "failed"
first_attempt_counts = tuple(agent.calls for agent in resume_agents)
resumed_result = asyncio.run(run_ingestion_ai(
    book_id,
    agentic_run_id = resume_run_id,
    storage = resume_storage,
    auditor = resume_agents[0],
    text_repair = resume_agents[1],
    structure_repair = resume_agents[2],
    metadata_normalizer = resume_agents[3],
    checkpointer = resume_checkpointer,
    resume = True,
))
assert resumed_result["status"] == "complete"
assert tuple(agent.calls for agent in resume_agents) == first_attempt_counts
assert json.loads(resume_storage.values[keys.agentic_manifest_key(book_id, resume_run_id)])["status"] == "done"
print("ok  failed publication resumes from its saved LangGraph checkpoint without rerunning agents")

queued_storage = FakeStorage(manifest, raw_pages)
queued_run = asyncio.run(create_ingestion_ai_run(
    book_id, agentic_run_id = "agent-run-queued", storage = queued_storage,
))
assert queued_run["status"] == "queued"
assert asyncio.run(read_ingestion_ai_run(
    book_id, "agent-run-queued", storage = queued_storage,
))["status"] == "queued"
asyncio.run(mark_ingestion_ai_run_failed(
    book_id, "agent-run-queued", "simulated worker failure", storage = queued_storage,
))
resumed_queue = asyncio.run(queue_ingestion_ai_resume(
    book_id, "agent-run-queued", storage = queued_storage,
))
assert resumed_queue["status"] == "queued" and resumed_queue["finished_at"] is None
print("ok  manual-dispatch preparation persists a pollable queued manifest")

fallback_storage = FakeStorage(manifest, raw_pages)
fallback_result = asyncio.run(run_ingestion_ai(
    book_id,
    agentic_run_id = "agent-run-003",
    storage = fallback_storage,
    auditor = InvalidAuditor(),
    text_repair = FakeTextRepair(),
    structure_repair = FakeStructureRepair(),
    metadata_normalizer = FakeMetadataNormalizer(),
))
fallback_manifest = json.loads(fallback_storage.values[keys.agentic_manifest_key(book_id, "agent-run-003")])
assert fallback_manifest["status"] == "done"
assert fallback_manifest["quality_status"] == "complete_with_review"
assert fallback_result["quality_report"]["structured_output_fallback_pages"] == [1]
print("ok  invalid structured audits are counted and fall back safely as unverified pages")


# --- Fixes from the 2026-11-05 assessment ---
site_filename = "The Art of Cyberwarfare An Investigators Guide (Jon DiMaggio) (z-library.sk, 1lib.sk, z-lib.sk).pdf"
site_stem = site_filename[:-4]
assert _matches_filename_label(site_stem, site_stem) and _matches_filename_label(site_filename, site_stem)
assert _fallback_title(
    {"filename": site_filename, "title": site_stem},
    [{"page_number": 1, "markdown": "<!-- image -->\n\n## The Art of Cyberwarfare\n\nGuide"}],
) == "The Art of Cyberwarfare"
assert _fallback_title({"filename": site_filename, "title": site_stem}, []) == (
    "The Art of Cyberwarfare An Investigators Guide (Jon DiMaggio)"
)
print("ok  a dotted download-site tag is not mistaken for a file extension and is stripped from filename titles")


def single_page_book(book: str, text: str) -> tuple[dict, dict[str, str], str]:
    extraction = "extract-single"
    page_key = keys.page_key(book, 1, extraction)
    return {
        "book_id": book, "extraction_id": extraction, "status": "done", "n_pages": 1,
        "filename": "an_example_book.epub",
        "pages": [{"page": 1, "key": page_key, "pictures": 0}],
    }, {page_key: text}, extraction


class ModerateMetadataNormalizer(FakeMetadataNormalizer):
    async def ainvoke(self, payload: dict, config: dict | None = None) -> dict:
        response = await super().ainvoke(payload, config)
        return {"structured_response": response["structured_response"].model_copy(update = {"confidence": 0.9})}


moderate_manifest, moderate_pages, _ = single_page_book("book-moderate", "An exam­ple book by A. Author\n")
moderate_result = asyncio.run(run_ingestion_ai(
    "book-moderate",
    agentic_run_id = "agent-run-moderate",
    storage = FakeStorage(moderate_manifest, moderate_pages),
    auditor = FakeAuditor(),
    text_repair = FakeTextRepair(),
    structure_repair = FakeStructureRepair(),
    metadata_normalizer = ModerateMetadataNormalizer(),
))
assert moderate_result["book_metadata"]["title"] == "An Example Book"
assert moderate_result["book_metadata"]["review_required"] is False
print("ok  a grounded metadata proposal at 0.9 confidence is accepted without the page-patch threshold")

noisy, noisy_counts = apply_mechanical_fixes("a\x07b ex­ample\n\tok")
assert noisy == "ab example\n\tok" and noisy_counts == {"control_characters": 1, "soft_hyphen": 1}
assert apply_mechanical_fixes("clean text") == ("clean text", {})
mechanical_manifest, mechanical_pages, mechanical_extraction = single_page_book(
    "book-mechanical", "An exam­ple book by A. Author \x07\n",
)
mechanical_storage = FakeStorage(mechanical_manifest, mechanical_pages)
mechanical_text_repair = FakeTextRepair()
mechanical_result = asyncio.run(run_ingestion_ai(
    "book-mechanical",
    agentic_run_id = "agent-run-mechanical",
    storage = mechanical_storage,
    auditor = FakeAuditor(),
    text_repair = mechanical_text_repair,
    structure_repair = FakeStructureRepair(),
    metadata_normalizer = FakeMetadataNormalizer(),
))
assert mechanical_text_repair.calls == 0
assert mechanical_result["quality_report"]["patches_auto_applied"] == 2
assert mechanical_result["quality_report"]["mechanical_fixes"] == {"control_characters": 1, "soft_hyphen": 1}
assert mechanical_result["quality_status"] == "complete"
assert mechanical_storage.values[keys.agentic_page_key("book-mechanical", "agent-run-mechanical", 1)] == (
    "An example book by A. Author \n"
)
assert mechanical_storage.values[keys.page_key("book-mechanical", 1, mechanical_extraction)].count("\x07") == 1
print("ok  control characters and soft hyphens are removed in code, never sent to a specialist, and the raw page is kept")

anchor_page = "## PART I\n\n## AN ADVANCED L ANDSCAPE\n"
anchor_evidence = schemas.EvidenceRef(
    source = schemas.EvidenceSource.PAGE_MARKDOWN, excerpt = "L ANDSCAPE", page_number = 1,
)
anchor_issue = schemas.PageIssue(
    issue_id = "i1", code = schemas.IssueCode.SPLIT_WORD, severity = schemas.IssueSeverity.MEDIUM,
    description = "A heading word is split.", confidence = 1.0, evidence = [anchor_evidence],
)


def anchored(target: str, context_before: str = "") -> schemas.PatchProposalBatch:
    return schemas.PatchProposalBatch(proposals = [schemas.PatchProposal(
        page_number = 1, issue_id = "i1", source_sha256 = "a" * 64, issue_code = schemas.IssueCode.SPLIT_WORD,
        risk = schemas.PatchRisk.LOW, context_before = context_before, target_text = target,
        replacement = "LANDSCAPE", rationale = "Join the split heading word.", confidence = 0.9,
        evidence = [anchor_evidence],
    )])


def validated(batch: schemas.PatchProposalBatch) -> list[schemas.PatchProposal]:
    return validate_proposals(
        batch, page_number = 1, source_sha256 = "a" * 64, markdown = anchor_page,
        assigned_issues = {"i1": anchor_issue}, role = schemas.SpecialistRole.TEXT_REPAIR,
    )


kept = validated(anchored("L ANDSCAPE", context_before = "A PARAPHRASED "))
assert len(kept) == 1 and kept[0].context_before == "" and kept[0].target_text == "L ANDSCAPE"
try:
    validated(anchored("L ANDSCAPES"))
except ValueError:
    pass
else:
    raise AssertionError("a target that is not on the page must still be rejected")
print("ok  a paraphrased patch anchor is dropped while an absent target is still rejected")


class FlakyTextRepair:
    def __init__(self):
        self.requests: list[dict] = []

    async def ainvoke(self, payload: dict, config: dict | None = None, context = None) -> dict:
        request = json.loads(payload["messages"][0]["content"])
        self.requests.append(request)
        if len(self.requests) == 1:
            raise ValueError("1 validation error for PatchProposalBatch: Input should be a valid dictionary")
        issue = request["assigned_issues"][0]
        return {"structured_response": schemas.PatchDraftBatch(proposals = [schemas.PatchDraft(
            issue_id = issue["issue_id"], risk = schemas.PatchRisk.LOW,
            target_text = "L ANDSCAPE", replacement = "LANDSCAPE",
            rationale = "Join the split heading word.", confidence = 0.9,
        )])}


retry_manifest, retry_pages, _ = single_page_book(
    "book-retry", "An exam­ple book by A. Author\n\nL ANDSCAPE OF IDEAS\n",
)
retry_storage = FakeStorage(retry_manifest, retry_pages)
flaky = FlakyTextRepair()
retry_result = asyncio.run(run_ingestion_ai(
    "book-retry",
    agentic_run_id = "agent-run-retry",
    storage = retry_storage,
    auditor = FakeAuditor(),
    text_repair = flaky,
    structure_repair = FakeStructureRepair(),
    metadata_normalizer = FakeMetadataNormalizer(),
))
assert len(flaky.requests) == 2
assert "previous_attempt_rejected" not in flaky.requests[0]
assert "valid dictionary" in flaky.requests[1]["previous_attempt_rejected"]
retry_proposals = json.loads(retry_storage.values[keys.agentic_patch_proposals_key("book-retry", "agent-run-retry", 1)])
assert len(retry_proposals["proposals"]) == 1 and retry_proposals["errors"] == []
assert set(retry_proposals["auto_applied_issue_ids"]) == {"p0001-split_word", "p0001-soft_hyphen"}
assert retry_result["quality_report"]["proposals_total"] == 1
assert retry_result["quality_status"] == "complete" and retry_result["quality_report"]["pages_requiring_review"] == []
print("ok  a rejected specialist response is retried once with the rejection reason, then its space-only repair is applied")

class RewritingTextRepair:
    """A confident model that changes letters, not just spaces."""

    async def ainvoke(self, payload: dict, config: dict | None = None, context = None) -> dict:
        request = json.loads(payload["messages"][0]["content"])
        return {"structured_response": schemas.PatchDraftBatch(proposals = [schemas.PatchDraft(
            issue_id = request["assigned_issues"][0]["issue_id"], risk = schemas.PatchRisk.LOW,
            target_text = "L ANDSCAPE", replacement = "LANDSCAPES",
            rationale = "Join the split heading word.", confidence = 0.99,
        )])}


review_manifest, review_pages, _ = single_page_book(
    "book-review", "An exam­ple book by A. Author\n\nL ANDSCAPE OF IDEAS\n",
)
review_storage = FakeStorage(review_manifest, review_pages)
review_result = asyncio.run(run_ingestion_ai(
    "book-review",
    agentic_run_id = "agent-run-review",
    storage = review_storage,
    auditor = FakeAuditor(),
    text_repair = RewritingTextRepair(),
    structure_repair = FakeStructureRepair(),
    metadata_normalizer = FakeMetadataNormalizer(),
))
assert review_result["quality_status"] == "complete_with_review"
assert review_result["quality_report"]["pages_requiring_review"] == [1]
print("ok  a repair that changes letters stays review-only however confident the model is")


class CallLimitedAuditor:
    """Loops past its model-call limit once, then answers like a healthy model."""

    def __init__(self):
        self.calls = 0

    async def ainvoke(self, payload: dict, config: dict | None = None) -> dict:
        self.calls += 1
        if self.calls == 1:
            return {"messages": [{"role": "assistant", "content": "Model call limit reached"}]}
        return await FakeAuditor().ainvoke(payload, config)


class CallLimitedTextRepair:
    def __init__(self):
        self.calls = 0

    async def ainvoke(self, payload: dict, config: dict | None = None, context = None) -> dict:
        self.calls += 1
        if self.calls == 1:
            return {"messages": [{"role": "assistant", "content": "Model call limit reached"}]}
        return await RewritingTextRepair().ainvoke(payload, config, context)


limit_manifest, limit_pages, _ = single_page_book(
    "book-limit", "An exam­ple book by A. Author\n\nL ANDSCAPE OF IDEAS\n",
)
limited_auditor, limited_repair = CallLimitedAuditor(), CallLimitedTextRepair()
limit_result = asyncio.run(run_ingestion_ai(
    "book-limit",
    agentic_run_id = "agent-run-limit",
    storage = FakeStorage(limit_manifest, limit_pages),
    auditor = limited_auditor,
    text_repair = limited_repair,
    structure_repair = FakeStructureRepair(),
    metadata_normalizer = FakeMetadataNormalizer(),
))
assert limited_auditor.calls == 2 and limited_repair.calls == 2
assert limit_result["quality_report"]["unverified_pages"] == []
assert limit_result["quality_report"]["proposals_total"] == 1
print("ok  a specialist or auditor that hits its model-call limit is retried once before the page degrades")

heading = "I NTRODUC T I ON"
duplicate_markdown = f"Intro\n\n{heading}\n\nBody"
split_evidence = lambda excerpt: [schemas.EvidenceRef(
    source = schemas.EvidenceSource.PAGE_MARKDOWN, excerpt = excerpt, page_number = 1,
)]
issue_of = lambda issue_id, code, excerpt: schemas.PageIssue(
    issue_id = issue_id, code = code, severity = schemas.IssueSeverity.LOW, description = "x",
    confidence = 0.9, evidence = split_evidence(excerpt),
)
merged_issues = merge_issues(
    [issue_of("p0001-split_word", schemas.IssueCode.SPLIT_WORD, heading)],
    [
        issue_of("llm-1", schemas.IssueCode.SPLIT_WORD, "NTRODUC T"),
        issue_of("llm-2", schemas.IssueCode.OTHER, "Body"),
        issue_of("llm-3", schemas.IssueCode.TOC_NOISE, "Intro"),
        issue_of("llm-4", schemas.IssueCode.TOC_NOISE, "Body"),
    ],
    duplicate_markdown,
)
assert [issue.issue_id for issue in merged_issues] == ["p0001-split_word", "llm-2", "llm-3"]
assert [evidence.excerpt for evidence in merged_issues[2].evidence] == ["Intro", "Body"]
print("ok  an auditor finding overlapping a deterministic one is merged and contents noise collapses to one issue")


class UngroundedAuditor:
    async def ainvoke(self, payload: dict, config: dict | None = None) -> dict:
        request = json.loads(payload["messages"][0]["content"])
        return {"structured_response": schemas.PageAssessment(
            page_number = request["page_number"], page_kind = schemas.PageKind.TEXT, confidence = 0.9,
            issues = [schemas.PageIssue(
                issue_id = "llm-ungrounded", code = schemas.IssueCode.OTHER,
                severity = schemas.IssueSeverity.LOW, description = "An invented defect.", confidence = 0.9,
                evidence = split_evidence("this text is not on the page"),
            )],
        )}


warning_manifest, warning_pages, _ = single_page_book("book-warning", "An exam­ple book by A. Author\n")
warning_result = asyncio.run(run_ingestion_ai(
    "book-warning",
    agentic_run_id = "agent-run-warning",
    storage = FakeStorage(warning_manifest, warning_pages),
    auditor = UngroundedAuditor(),
    text_repair = FakeTextRepair(),
    structure_repair = FakeStructureRepair(),
    metadata_normalizer = FakeMetadataNormalizer(),
))
assert warning_result["quality_report"]["audit_warning_count"] == 2  # discarded excerpt + discarded finding
assert warning_result["quality_report"]["pages_requiring_review"] == []
assert warning_result["quality_status"] == "complete"
print("ok  a handled audit warning is reported but does not by itself send the page to review")
assert fallback_result["quality_report"]["review_reasons"] == {}
print("ok  review pages are broken down by reason")

# --- What the reader can ask for ---
display_ai, display_ai_source = asyncio.run(read_display_page("book-mechanical", 1, storage = mechanical_storage))
assert display_ai_source == "agentic" and display_ai == "An example book by A. Author \n"
display_raw, display_raw_source = asyncio.run(
    read_display_page("book-mechanical", 1, storage = mechanical_storage, source = "docling"),
)
assert display_raw_source == "docling" and display_raw == "An exam­ple book by A. Author \x07\n"
pinned_ai, pinned_ai_source = asyncio.run(
    read_display_page("book-mechanical", 1, storage = mechanical_storage, source = "agentic"),
)
assert pinned_ai_source == "agentic" and pinned_ai == display_ai
raw_only_storage = FakeStorage(mechanical_manifest, mechanical_pages)
assert asyncio.run(
    read_display_page("book-mechanical", 1, storage = raw_only_storage, source = "agentic"),
) == (None, "agentic")
print("ok  a page can be read raw, AI-processed, or as the server chooses, and a missing AI page is not faked")

mechanical_quality = asyncio.run(read_page_quality("book-mechanical", 1, storage = mechanical_storage))
assert mechanical_quality["status"] == "done" and mechanical_quality["delivery_disposition"] == "include"
assert mechanical_quality["mechanical_fixes"] == {"control_characters": 1, "soft_hyphen": 1}
assert {issue["code"]: issue["resolution"] for issue in mechanical_quality["issues"]} == {
    "control_characters": "auto_applied",
    "soft_hyphen": "auto_applied",
}
spacing_quality = asyncio.run(read_page_quality("book-retry", 1, storage = retry_storage))
assert spacing_quality["status"] == "done"
split_issue = next(issue for issue in spacing_quality["issues"] if issue["code"] == "split_word")
assert split_issue["resolution"] == "auto_applied" and split_issue["proposal"]["replacement"] == "LANDSCAPE"
review_quality = asyncio.run(read_page_quality("book-review", 1, storage = review_storage))
assert review_quality["status"] == "review_required"
review_issue = next(issue for issue in review_quality["issues"] if issue["code"] == "split_word")
assert review_issue["resolution"] == "review" and review_issue["proposal"]["replacement"] == "LANDSCAPES"
assert asyncio.run(read_page_quality("book-mechanical", 99, storage = mechanical_storage)) is None
assert asyncio.run(read_page_quality("book-mechanical", 1, storage = raw_only_storage)) is None
print("ok  the page quality report says what was fixed, what needs review, and the suggested fix")
