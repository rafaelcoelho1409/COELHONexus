"""Offline checks of the code ledger of a chapter: distinct blocks, variants of one example, the canonical variant, what must be taught, the summary an
agent reads, and the reading-time estimate. Pure: no storage, no model.

    cd apps/server && .venv/bin/python ../../scripts/server/test_dd_ledger.py
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "server"))

import domains

os.environ.setdefault("DD_STUDY_SEM", "1")

ledger = domains.dd.pipeline.ledger
domain = ledger.domain
vault = domains.dd.ingestion.vault


def check(label: str, got, want) -> None:
    assert got == want, f"{label}: expected {want!r}, got {got!r}"
    print(f"ok  {label}")


def entries_of(markdown: str) -> dict:
    return vault.domain.sentinelize_doc(markdown)[1]


FULL = (
    "from deepagents import create_deep_agent\n\n\ndef get_weather(city: str) -> str:\n    return f\"It is sunny in {city}\"\n\n\n"
    "agent = create_deep_agent(tools=[get_weather])\nresult = agent.invoke({\"messages\": [{\"role\": \"user\", \"content\": \"weather in Paris\"}]})\n"
    "print(result)"
)
# the same example with the imports dropped and a renamed variable: a variant, and a worse one
BARE = FULL.replace("from deepagents import create_deep_agent\n\n\n", "").replace("result", "answer")
OTHER = (
    "from deepagents.backends import StateBackend\n\nbackend = StateBackend()\nfiles = backend.ls('/')\nbackend.write_file('/notes.md', 'hello')\n"
    "content = backend.read_file('/notes.md')\nprint(content)"
)
OLD = "# legacy way, deprecated\nfrom oldlib import make_widget\n\nwidget = make_widget(size=3)\nwidget.configure(color='red')\nwidget.show()\nwidget.close()"

PAGE_A = f"# Quickstart\n\nInstall it.\n\n```bash\npip install deepagents\n```\n\nThen:\n\n```python\n{FULL}\n```\n\nExample output:\n\n```text\nIt is sunny in Paris\n```\n"
PAGE_B = f"# Agents\n\nThe same agent, shorter:\n\n```python\n{BARE}\n```\n\nStorage:\n\n```python\n{OTHER}\n```\n\nOne call: `x`\n\n```python\nagent.invoke(q)\n```\n"
PAGE_C = f"# Old\n\n```python\n{OLD}\n```\n\nAnd the agent again:\n\n```python\n{FULL}\n```\n\nConfig:\n\n```json\n{{\"a\": 1}}\n```\n"


def test_fence_body_and_roles() -> None:
    check("the fence markers are taken off", domain.fence_body("```python\nx = 1\n```"), "x = 1")
    check("a block quote's prefix and indentation are taken off",
          domain.fence_body("> ```python\n>   x = 1\n>   y = 2\n> ```"), "x = 1\ny = 2")
    check("roles by language and content",
          [domain.role_of(lang, body) for lang, body in (
              ("bash", "pip install deepagents"), ("bash", "uv run main.py"), ("json", "{}"), ("text", "hi"), ("python", "x = 1"))],
          ["setup", "code", "config", "output", "code"])
    check("python facts: parses, imports, defines", domain.python_facts("import os\nfrom a.b import c\n\ndef f():\n    pass\n\nclass K: ..."),
          (True, ("os", "a"), ("f", "K")))
    check("python that does not parse still lists what it defines", domain.python_facts("def broken(:\n    pass\ndef ok(x):\n    return x"), (False, (), ("broken", "ok")))
    check("an interactive session has no verdict", domain.python_facts(">>> 1 + 1\n2")[0], None)


def test_ledger() -> None:
    pages = [("a.md", entries_of(PAGE_A)), ("b.md", entries_of(PAGE_B)), ("c.md", entries_of(PAGE_C))]
    built = domain.build(pages)
    by_lang = {}
    for block in built.blocks.values():
        by_lang.setdefault((block.lang, block.role), []).append(block)
    check("every distinct block is one entry (the full agent appears on two pages)", len(built.blocks), 8)
    full = next(block for block in built.blocks.values() if block.body == FULL)
    check("an identical block on two pages is one entry that knows both pages", full.pages, ("a.md", "c.md"))
    check("roles: setup, output and config are not code",
          sorted((block.role for block in built.blocks.values())), ["code"] * 5 + ["config", "output", "setup"])
    bare = next(block for block in built.blocks.values() if block.body == BARE)
    family = next(family for family in built.families if full.hash in family.hashes)
    check("the bare variant joins the full agent's family", bare.hash in family.hashes, True)
    check("the canonical variant is the one with its imports, on more pages", family.canonical, full.hash)
    check("the family knows every page", family.pages, ("a.md", "c.md", "b.md"))
    old = next(block for block in built.blocks.values() if "make_widget" in block.body)
    check("a block marked deprecated scores below the others", old.score < full.score, True)
    check("what must be taught: one canonical per code family, no one-liner, in the order the pages introduce them",
          [built.blocks[digest].body[:22] for digest in built.must_teach],
          [FULL[:22], OTHER[:22], OLD[:22]])
    check("the install line is setup, shown once, not taught", [built.blocks[digest].body for digest in built.setup], ["pip install deepagents"])
    check("the one-line call is in the ledger but is not taught", ("agent.invoke(q)" in {block.body for block in built.blocks.values()}, built.blocks[next(
        digest for digest, block in built.blocks.items() if block.body == "agent.invoke(q)")].hash in built.must_teach), (True, False))
    check("the pages are kept in reading order", built.pages, ("a.md", "b.md", "c.md"))
    # coverage
    check("a chapter that used nothing has everything left", len(domain.uncovered(built, set())), 3)
    check("using a variant counts as teaching its family", [built.blocks[digest].body[:22] for digest in domain.uncovered(built, {bare.hash})],
          [OTHER[:22], OLD[:22]])
    check("using the canonical blocks leaves nothing", domain.uncovered(built, set(built.must_teach)), [])
    # determinism
    check("the same pages give the same ledger", domain.build(pages) == built, True)


def test_summary_and_minutes() -> None:
    built = domain.build([("a.md", entries_of(PAGE_A)), ("b.md", entries_of(PAGE_B))])
    rows = domain.summary(built)
    check("the summary lists what to teach, then the setup", [row["role"] for row in rows], ["code", "code", "setup"])
    check("each row says what the block defines and how many variants it has",
          (rows[0]["defines"], rows[0]["variants"], rows[0]["pages"]), (["get_weather"], 1, 2))
    check("a preview is a few lines", len(rows[0]["preview"].splitlines()), 3)
    check("the summary respects its limit", len(domain.summary(built, limit = 1)), 1)
    check("prose and code are counted apart", domain.page_stats("One two three.\n\n```python\nx = 1\n\ny = 2\n```\n\nFour five.\n"), (5, 2))
    check("a tilde fence is a fence too", domain.page_stats("a b\n~~~\nx\n~~~\n"), (2, 1))
    check("reading minutes: 230 words a minute, 3 seconds a line of code", domain.reading_minutes(230, 20), 2.0)
    check("a page's minutes", domain.pages_minutes(["word " * 230 + "\n```\n" + "x\n" * 20 + "```\n"]), 2.0)


class FakeBlobs:
    def __init__(self, bodies: dict[str, str]) -> None:
        self.bodies = bodies

    async def read_many(self, keys):
        return [self.bodies.get(key, "") for key in keys]


async def test_service() -> None:
    blobs = FakeBlobs({"a.md": PAGE_A, "b.md": PAGE_B})
    built = await ledger.service.build(blobs, "fw", ["a.md", "missing.md", "b.md"])
    check("the service builds from raw pages and skips an empty one", (built.pages, len(built.must_teach)), (("a.md", "b.md"), 2))


async def main() -> None:
    test_fence_body_and_roles()
    test_ledger()
    test_summary_and_minutes()
    await test_service()
    print("\nledger: all checks passed")


if __name__ == "__main__":
    asyncio.run(main())
