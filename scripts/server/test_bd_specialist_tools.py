"""Offline checks of the Book Distiller specialist tools: the patch pre-flight, the book lookups and the agent wiring.

    cd apps/server && .venv/bin/python ../../scripts/server/test_bd_specialist_tools.py
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "server"))

import domains
import domains.bd.ingestion.ai.params
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_openai import ChatOpenAI

from deepagents import HarnessProfile, register_harness_profile

from ai.bd import tools
from ai.bd.agents import schemas
from ai.bd.agents.ingestion import structure_repair, text_repair
from ai.bd.nodes.ingestion.repair_pages import domain, service


def check(label: str, got, want) -> None:
    assert got == want, f"{label}: expected {want!r}, got {got!r}"
    print(f"ok  {label}")


def contains(label: str, text: str, fragment: str) -> None:
    assert fragment in text, f"{label}: expected {fragment!r} in {text!r}"
    print(f"ok  {label}")



# --- check_draft: what the gates would do with one patch
SOURCE = (
    "The cyber­ attacks grew.\n"
    "See https://www .site.com/a for ACKNOWL EDGMENT S and again https://www .site.com/a.\n"
    "Plain text with a lonely word nationstate here.\n"
)
SHA = "0" * 64


def issue(issue_id: str, excerpt: str, code = schemas.IssueCode.SPLIT_WORD) -> schemas.PageIssue:
    return schemas.PageIssue(
        issue_id = issue_id,
        code = code,
        severity = schemas.IssueSeverity.LOW,
        description = "a defect",
        confidence = 0.9,
        evidence = [schemas.EvidenceRef(
            source = schemas.EvidenceSource.PAGE_MARKDOWN,
            excerpt = excerpt,
            page_number = 1,
        )],
    )


ASSIGNED = {
    "soft": issue("soft", "cyber­ attacks"),
    "url": issue("url", "https://www .site.com/a"),
    "ack": issue("ack", "ACKNOWL EDGMENT S"),
    "nation": issue("nation", "nationstate"),
}


def preflight(issue_id: str, target: str, replacement: str) -> str:
    return domain.check_draft(
        issue_id = issue_id,
        target_text = target,
        replacement = replacement,
        markdown = SOURCE,
        page_number = 1,
        source_sha256 = SHA,
        assigned_issues = ASSIGNED,
        role = schemas.SpecialistRole.TEXT_REPAIR,
        allowed_context = SOURCE,
    )


contains("a space-only repair is applied automatically", preflight("url", "https://www .site.com/a", "https://www.site.com/a"), "applies it automatically")
contains("changed letters go to review", preflight("ack", "ACKNOWL EDGMENT S", "ACKNOWLEDGEMENTS"), "kept for human review")
contains("a soft hyphen turned visible goes to review", preflight("soft", "cyber­ attacks", "cyber-attacks"), "kept for human review")
contains("a joined soft-hyphen word is automatic", preflight("soft", "cyber­ attacks", "cyberattacks"), "applies it automatically")
contains("a target the page lacks is rejected", preflight("nation", "nation state", "nation-state"), "rejected")
contains("an issue that is not assigned is rejected", preflight("other", "nationstate", "nation-state"), "not one of your assigned issues")
contains("a no-op patch is rejected", preflight("nation", "nationstate", "nationstate"), "rejected")
contains(
    "an invented link is rejected",
    preflight("nation", "nationstate", "[nation](https://evil.example)"),
    "rejected",
)

# --- book lookups
counts = domain.count_variants(
    "Cyberattack and cyber-attack.\nA cyber\nattack, the cyberattack; cyber­attack",
    ["cyberattack", "cyber-attack", "cyber attack", ""],
    max_variants = 8,
    max_chars = 80,
)
check("spellings are counted as whole words, across line breaks and soft hyphens", counts.splitlines(), [
    "'cyberattack': 3",
    "'cyber-attack': 1",
    "'cyber attack': 1",
])
check("only the last lines of a page are kept for the previous page", domain.edge_lines("a\n\nb\nc\n", 2, from_end = True, max_chars = 10), ["b", "c"])
check("only the first lines of a page are kept for the next page", domain.edge_lines("a\n\nb\nc\n", 2, from_end = False, max_chars = 10), ["a", "b"])

PAGES = {1: "Page one ends\nwith a sentence that", 2: "continues here.\nSecond line", 3: "Third page"}
reads: list[str] = []


async def read_page(key: str) -> str:
    reads.append(key)
    return PAGES[int(key)]


book = service.BookIndex(read_page, [{"page": number, "source_key": str(number)} for number in PAGES])
edges = asyncio.run(book.page_edges(2, 1))
contains("the previous page's last line is shown", edges, "with a sentence that")
contains("the next page's first line is shown", edges, "Third page")
check("no page is read twice", sorted(reads), ["1", "2", "3"])
contains("the first page has no previous page", asyncio.run(book.page_edges(1, 3)), "previous page: none")
contains("the book's spellings are counted from the pages", asyncio.run(book.term_counts(["third page"])), "'third page': 1")
check("the pages stay cached", len(reads), 3)


# --- wiring: a real DeepAgent built from the specialist builder, driven by a scripted model
class Script:
    def __init__(self, patch: dict):
        self.patch = patch
        self.tool_replies: list[str] = []
        self.seen_tools: set[str] = set()
        self.calls = 0

    async def generate(self, messages, **kwargs) -> ChatResult:
        self.calls += 1
        replies = [message for message in messages if isinstance(message, ToolMessage)]
        self.tool_replies = [str(message.content) for message in replies]
        names = [tool["function"]["name"] for tool in kwargs.get("tools", [])]
        self.seen_tools |= set(names)
        if not replies:
            message = AIMessage(content = "", tool_calls = [{
                "name": "check_patch",
                "args": {"issue_id": "url", "target_text": self.patch["target_text"], "replacement": self.patch["replacement"]},
                "id": "call-check",
            }])
        elif "PatchDraftBatch" in names:
            message = AIMessage(content = "", tool_calls = [{
                "name": "PatchDraftBatch",
                "args": {"proposals": [{**self.patch, "issue_id": "url", "risk": "low", "rationale": "stray space", "confidence": 0.99}]},
                "id": "call-answer",
            }])
        else:
            message = AIMessage(content = json.dumps({"proposals": [{
                **self.patch, "issue_id": "url", "risk": "low", "rationale": "stray space", "confidence": 0.99,
            }]}))
        return ChatResult(generations = [ChatGeneration(message = message)])


def scripted_model(script: Script) -> ChatOpenAI:
    model = ChatOpenAI(model = "scripted-model", api_key = "unused", base_url = "http://127.0.0.1:9", max_retries = 0)

    async def _agenerate(messages, stop = None, run_manager = None, **kwargs):
        return await script.generate(messages, **kwargs)

    object.__setattr__(model, "_agenerate", _agenerate)
    return model


script = Script({"target_text": "https://www .site.com/a", "replacement": "https://www.site.com/a"})
# Other code in the worker (a Docs Distiller role, say) may have registered an exclusion under the same model name; DeepAgents
# merges those, so without isolation it would hide read_file from this role too.
register_harness_profile("openai:scripted-model", HarnessProfile(excluded_tools = frozenset({"read_file", "check_patch"})))
agent = text_repair.agent.text_repair(scripted_model(script))
specialist_context = domains.bd.ingestion.ai.entities.SpecialistContext(
    check_patch = lambda issue_id, target, replacement: preflight(issue_id, target, replacement),
    term_counts = book.term_counts,
    page_edges = book.page_edges,
)
result = asyncio.run(agent.ainvoke(
    {"messages": [{"role": "user", "content": "repair"}]},
    context = specialist_context,
))
check("the specialist called check_patch and read its answer", len(script.tool_replies), 1)
contains("the tool result reached the model", script.tool_replies[0], "applies it automatically")
structured = result.get("structured_response")
check("the specialist's final answer is the structured batch", getattr(structured, "proposals", [None])[0].target_text, "https://www .site.com/a")
check(
    "the role sees its own tools and the skill reader, whatever another feature excluded, and nothing else",
    sorted(script.seen_tools - {"PatchDraftBatch"}),
    sorted({
        "read_file",
        tools.patch.service.check_patch.name,
        tools.book.service.book_term_frequency.name,
        tools.book.service.adjacent_page_edges.name,
    }),
)

structure_script = Script({"target_text": "x", "replacement": "y"})
structure_agent = structure_repair.agent.structure_repair(scripted_model(structure_script))
asyncio.run(structure_agent.ainvoke(
    {"messages": [{"role": "user", "content": "repair"}]},
    context = specialist_context,
))
check(
    "a role built after it in the same process gets its own set, not the first role's",
    sorted(structure_script.seen_tools - {"PatchDraftBatch"}),
    sorted({
        "read_file",
        tools.patch.service.check_patch.name,
        tools.book.service.adjacent_page_edges.name,
    }),
)

print("specialist tools passed")
