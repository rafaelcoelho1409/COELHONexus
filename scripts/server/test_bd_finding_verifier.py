"""Offline checks of the Book Distiller finding verifier (the second reader of judgement-call audit findings).

    cd apps/server && .venv/bin/python ../../scripts/server/test_bd_finding_verifier.py
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "server"))

import domains
import domains.bd.ingestion.ai.params
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_openai import ChatOpenAI

from ai.bd.agents import schemas
from ai.bd.agents.ingestion import finding_verifier
from ai.bd.nodes.ingestion.audit_batch import domain, service


def check(label: str, got, want) -> None:
    assert got == want, f"{label}: expected {want!r}, got {got!r}"
    print(f"ok  {label}")


MARKDOWN = "Body text with a picture.\n\n<!-- image -->\n\nFigure 1: A caption.\n\nAT TACKS grew.\n"


def issue(issue_id: str, code, excerpt: str, confidence: float = 0.9) -> schemas.PageIssue:
    return schemas.PageIssue(
        issue_id = issue_id,
        code = code,
        severity = schemas.IssueSeverity.LOW,
        description = "reported by the auditor",
        confidence = confidence,
        evidence = [schemas.EvidenceRef(
            source = schemas.EvidenceSource.PAGE_MARKDOWN,
            excerpt = excerpt,
            page_number = 3,
        )],
    )


ISSUES = [
    issue("picture", schemas.IssueCode.STRUCTURE, "<!-- image -->"),
    issue("split", schemas.IssueCode.SPLIT_WORD, "AT TACKS"),
    issue("unsure", schemas.IssueCode.SPLIT_WORD, "Body text", confidence = 0.5),
    issue("vague", schemas.IssueCode.OTHER, "Figure 1: A caption."),
]

candidates = domain.select_candidates(ISSUES)
check("vague codes and low confidence are checked, a confident concrete defect is not", [i.issue_id for i in candidates], ["picture", "unsure", "vague"])

payload = json.loads(domain.build_verification_payload(3, candidates, MARKDOWN))
check("the payload names each finding", [f["issue_id"] for f in payload["findings"]], ["picture", "unsure", "vague"])
check("the payload carries page text around the evidence", "<!-- image -->" in payload["page_excerpts"], True)


def batch(**verdicts: str) -> schemas.FindingCheckBatch:
    return schemas.FindingCheckBatch(checks = [
        schemas.FindingCheck(issue_id = key, verdict = schemas.FindingVerdict(value), reason = "because")
        for key, value in verdicts.items()
    ])


kept, warnings = domain.apply_checks(ISSUES, candidates, batch(picture = "not_a_defect", vague = "defect", split = "not_a_defect"))
check("a dismissed candidate is dropped", [i.issue_id for i in kept], ["split", "unsure", "vague"])
check("a finding that was not shown cannot be dismissed", "split" in [i.issue_id for i in kept], True)
check("the dismissal is reported", len(warnings), 1)
kept, warnings = domain.apply_checks(ISSUES, candidates, batch())
check("a verifier that names nothing keeps everything", [i.issue_id for i in kept], [i.issue_id for i in ISSUES])
check("and says nothing", warnings, [])


class Verifier:
    def __init__(self, result = None, error: Exception | None = None):
        self.result = result
        self.error = error
        self.calls = 0

    async def ainvoke(self, payload, config = None):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result


async def run(verifier, issues = ISSUES):
    return await service.verify_findings(verifier, run_id = "run-1", page_number = 3, markdown = MARKDOWN, issues = issues)


kept, warnings = asyncio.run(run(Verifier({"structured_response": batch(picture = "not_a_defect")})))
check("the service drops what the verifier dismisses", [i.issue_id for i in kept], ["split", "unsure", "vague"])

failing = Verifier(error = RuntimeError("provider down"))
kept, warnings = asyncio.run(run(failing))
check("a failed verifier keeps every finding", [i.issue_id for i in kept], [i.issue_id for i in ISSUES])
check("and says it was unavailable", "unavailable" in warnings[0], True)

kept, warnings = asyncio.run(run(Verifier({"messages": []})))
check("an answer without a structured response keeps every finding", len(kept), len(ISSUES))

confident_only = Verifier({"structured_response": batch()})
kept, _ = asyncio.run(run(confident_only, [ISSUES[1]]))
check("nothing is asked when no finding is a judgement call", confident_only.calls, 0)


# --- wiring: the real DeepAgent built by the factory, driven by a scripted model
class Script:
    def __init__(self):
        self.seen_tools: list[str] = []

    async def generate(self, messages, **kwargs) -> ChatResult:
        self.seen_tools = [tool["function"]["name"] for tool in kwargs.get("tools", [])]
        checks = [{"issue_id": "picture", "verdict": "not_a_defect", "reason": "normal Docling output"}]
        if "FindingCheckBatch" in self.seen_tools:
            message = AIMessage(content = "", tool_calls = [{"name": "FindingCheckBatch", "args": {"checks": checks}, "id": "call-1"}])
        else:
            message = AIMessage(content = json.dumps({"checks": checks}))
        return ChatResult(generations = [ChatGeneration(message = message)])


script = Script()
model = ChatOpenAI(model = "scripted-model", api_key = "unused", base_url = "http://127.0.0.1:9", max_retries = 0)


async def _agenerate(messages, stop = None, run_manager = None, **kwargs):
    return await script.generate(messages, **kwargs)


object.__setattr__(model, "_agenerate", _agenerate)
agent = finding_verifier.agent.finding_verifier(model)
result = asyncio.run(agent.ainvoke({"messages": [{"role": "user", "content": domain.build_verification_payload(3, candidates, MARKDOWN)}]}))
check("the factory builds an agent whose answer is the structured batch", result["structured_response"].checks[0].issue_id, "picture")
check("the verifier can call no tool of its own", [name for name in script.seen_tools if name not in {"FindingCheckBatch", "read_file"}], [])

print("finding verifier passed")
