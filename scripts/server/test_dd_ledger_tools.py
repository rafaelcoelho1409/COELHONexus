"""Offline checks of the ledger tools an agent reads a chapter's code with: variants, one block, a diff of two variants, a search by names.

    cd apps/server && .venv/bin/python ../../scripts/server/test_dd_ledger_tools.py
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "server"))

import ai
import domains

os.environ.setdefault("DD_STUDY_SEM", "1")

domain = domains.dd.pipeline.ledger.domain
ledger_tools = ai.dd.tools.pipeline.synth.ledger.tool.ledger_tools

FULL = (
    "from deepagents import create_deep_agent\n\n\ndef get_weather(city: str) -> str:\n    return f\"It is sunny in {city}\"\n\n\n"
    "agent = create_deep_agent(tools=[get_weather])\nresult = agent.invoke({\"messages\": [{\"role\": \"user\", \"content\": \"weather in Paris\"}]})\n"
    "print(result)"
)
BARE = FULL.replace("from deepagents import create_deep_agent\n\n\n", "").replace("result", "answer")
OTHER = (
    "from deepagents.backends import StateBackend\n\nbackend = StateBackend()\nfiles = backend.ls('/')\nbackend.write_file('/notes.md', 'hello')\n"
    "content = backend.read_file('/notes.md')\nprint(content)"
)
PAGE = f"# Agents\n\n```python\n{FULL}\n```\n\n```python\n{BARE}\n```\n\n```python\n{OTHER}\n```\n"


def check(label: str, got, want) -> None:
    assert got == want, f"{label}: expected {want!r}, got {got!r}"
    print(f"ok  {label}")


def truthy(label: str, value) -> None:
    assert value, f"{label}: expected a truthy value, got {value!r}"
    print(f"ok  {label}")


async def main() -> None:
    built = domain.build([("a.md", domains.dd.ingestion.vault.domain.sentinelize_doc(PAGE)[1])])
    tools = {tool.name: tool for tool in ledger_tools(built)}
    check("four tools", sorted(tools), ["compare_variants", "get_block", "list_variants", "search_code"])
    full = next(block for block in built.blocks.values() if block.body == FULL)
    bare = next(block for block in built.blocks.values() if block.body == BARE)

    listed = await tools["list_variants"].ainvoke({"hash": full.hash})
    truthy("list_variants: both variants, the canonical first", listed.startswith("2 variant(s)") and listed.index(full.hash) < listed.index(bare.hash))
    truthy("list_variants: says which is canonical", "the canonical variant" in listed and "a variant" in listed)

    shown = await tools["get_block"].ainvoke({"hash": f"  {full.hash} "})
    truthy("get_block: the code and its facts, hash trimmed", "create_deep_agent(tools=[get_weather])" in shown and "defines get_weather" in shown)

    diff = await tools["compare_variants"].ainvoke({"first": full.hash, "second": bare.hash})
    truthy("compare_variants: a diff of the two", "-from deepagents import create_deep_agent" in diff and "+answer = agent.invoke" in diff)
    same = await tools["compare_variants"].ainvoke({"first": full.hash, "second": full.hash})
    check("compare_variants: the same block", same, "The two blocks are the same code.")

    found = await tools["search_code"].ainvoke({"query": "StateBackend write_file"})
    truthy("search_code: finds the block that uses the names", found.startswith("- ") and "matches 2 of 2 names" in found)
    none = await tools["search_code"].ainvoke({"query": "nonexistentname"})
    truthy("search_code: says when nothing matches", none.startswith("No block in this chapter uses"))
    empty = await tools["search_code"].ainvoke({"query": "a b"})
    check("search_code: asks for names", empty, "Give at least one name to look for.")

    for name, args in (("list_variants", {"hash": "nope"}), ("get_block", {"hash": "nope"}), ("compare_variants", {"first": "nope", "second": full.hash})):
        truthy(f"{name}: an unknown hash is explained, not raised", (await tools[name].ainvoke(args)).startswith("No block 'nope'"))
    print("\nledger tools: all checks passed")


if __name__ == "__main__":
    asyncio.run(main())
