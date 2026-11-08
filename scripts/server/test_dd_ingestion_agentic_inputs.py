"""Offline checks of the pages the Docs Distiller agentic ingestion freezes as its inputs: the deterministic normalizer runs on each page before the
audit reads it (the host's banner, footer links and fence attributes are gone), and a resumed run keeps the exact bytes its failed run froze. The graph
and the agents are replaced by recorders; storage is in memory.

    cd apps/server && .venv/bin/python ../../scripts/server/test_dd_ingestion_agentic_inputs.py
"""
import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "server"))

import ai
import domains

os.environ.setdefault("DD_STUDY_SEM", "1")

service = domains.dd.ingestion.ai.service
keys = domains.dd.ingestion.storage.keys
SLUG = "fw"
RUN = "run1"

BANNER = "> ## Documentation Index\n> Fetch the complete documentation index at: https://docs.example.com/llms.txt\n> Use this file to discover all available pages before exploring further.\n\n"
FOOTER = (
    "\n***\n\n[Connect these docs](https://docs.example.com/use-these-docs.md) to your agent of choice via MCP for real-time answers.\n\n"
    "[Edit this page on GitHub](https://github.com/o/r/edit/main/a.mdx) or [file an issue](https://github.com/o/r/issues/new/choose).\n"
)
BODY = "# Intro\n\nUse the client:\n\n```python theme={null}\nclient.run()\n```\n"
RAW = BANNER + BODY + FOOTER
CLEAN = "# Intro\n\nUse the client:\n\n```python\nclient.run()\n```\n"


def check(label: str, got, want) -> None:
    assert got == want, f"{label}: expected {want!r}, got {got!r}"
    print(f"ok  {label}")


class FakeBlobs:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def exists(self, key):
        return key in self.objects

    async def read_text(self, key, encoding = "utf-8"):
        return self.objects[key].decode(encoding)

    async def read_bytes(self, key):
        return self.objects[key]

    async def write(self, key, content, content_type = "text/markdown"):
        self.objects[key] = content.encode("utf-8") if isinstance(content, str) else content
        return len(self.objects[key])


class FakeStore:
    def __init__(self, blobs: FakeBlobs, source_key: str) -> None:
        self.framework_slug = SLUG
        self.run_id = RUN
        self.minio = blobs
        self.manifest = [SimpleNamespace(
            idx = 0, slug = "intro", url = "https://docs.example.com/intro", source_path = "https://docs.example.com/intro", tier = "sitemap",
            title = "Intro", section = "", notes = "", lastmod = "", anchors = [], source_key = source_key, key = source_key, raw_key = "raw/0.md",
            bytes = 0,
        )]

    async def replace_manifest(self, entries):
        self.manifest = list(entries)


class FakeGraph:
    def __init__(self) -> None:
        self.seen: dict | None = None

    async def ainvoke(self, graph_input, config = None, context = None):
        self.seen = graph_input
        page = graph_input["pages"][0]
        return {
            "output_pages": [{"idx": page["idx"], "key": page["source_key"], "bytes": page["bytes"], "title": page["title"],
                              "raw_key": page["raw_key"], "source_key": page["source_key"]}],
            "summary": {},
        }


async def run(store: FakeStore) -> FakeGraph:
    graph = FakeGraph()
    domains.settings.chat.service.refresh_endpoint = lambda force = False: None
    ai.dd.agents.ingestion.page_auditor.agent.page_auditor = lambda *a, **k: object()
    ai.dd.agents.ingestion.page_repair.agent.page_repair = lambda *a, **k: object()
    ai.dd.graphs.ingestion.graphs.ingestion_graph = lambda checkpointer = None: graph
    await service.run_ingestion_agentic(store)
    return graph


async def test_normalize_source() -> None:
    normalized, mechanical_fixes = await service.normalize_source(RAW)
    check("the banner, the footer links, the rule above them and the fence attribute go", normalized, CLEAN)
    check("normalization reports the automatic cleanup", bool(mechanical_fixes), True)
    normalized_again, fixes_again = await service.normalize_source(CLEAN)
    check("a normalized page is unchanged by a second pass", normalized_again, CLEAN)
    check("a second pass reports no automatic cleanup", fixes_again, {})


async def test_fresh_run() -> None:
    blobs = FakeBlobs()
    blobs.objects["raw/page.md"] = RAW.encode("utf-8")
    store = FakeStore(blobs, "raw/page.md")
    graph = await run(store)
    input_key = keys.agentic_input_key(SLUG, RUN, 0, "intro")
    check("the frozen input is the normalized page", blobs.objects[input_key].decode("utf-8"), CLEAN)
    page = graph.seen["pages"][0]
    check("the graph is given the normalized input", (page["source_key"], page["bytes"]), (input_key, len(CLEAN.encode("utf-8"))))
    check("…and its hash is the normalized text's", page["source_sha256"], domains.dd.ingestion.ai.domain.sha256_text(CLEAN))
    check("the page the site sent is not touched", blobs.objects["raw/page.md"].decode("utf-8"), RAW)


async def test_resume_keeps_frozen_bytes() -> None:
    blobs = FakeBlobs()
    input_key = keys.agentic_input_key(SLUG, RUN, 0, "intro")
    frozen = "# Intro\n\nFrozen by the failed run.\n"
    blobs.objects[input_key] = frozen.encode("utf-8")
    blobs.objects["raw/page.md"] = RAW.encode("utf-8")
    store = FakeStore(blobs, input_key)
    graph = await run(store)
    check("a resumed run reads the bytes its failed run froze", blobs.objects[input_key].decode("utf-8"), frozen)
    check("…and audits them", graph.seen["pages"][0]["source_sha256"], domains.dd.ingestion.ai.domain.sha256_text(frozen))


async def test_unreadable_page_is_audited_as_it_came() -> None:
    def broken(source):
        raise ValueError("boom")

    real = domains.dd.ingestion.corpus_normalize.domain.normalize_doc
    domains.dd.ingestion.corpus_normalize.domain.normalize_doc = broken
    try:
        normalized, mechanical_fixes = await service.normalize_source(RAW)
        check("a page the normalizer cannot read is kept as it came", normalized, RAW)
        check("a failed normalizer reports no automatic cleanup", mechanical_fixes, {})
    finally:
        domains.dd.ingestion.corpus_normalize.domain.normalize_doc = real


async def main() -> None:
    await test_normalize_source()
    await test_fresh_run()
    await test_resume_keeps_frozen_bytes()
    await test_unreadable_page_is_audited_as_it_came()
    print("\nagentic inputs: all checks passed")


if __name__ == "__main__":
    asyncio.run(main())
