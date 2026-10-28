"""Offline check of how the Ingestion explorer's page titles are made (`domains.dd.ingestion`).

    cd apps/server && .venv/bin/python ../../scripts/server/test_ingestion_titles.py

`display_title` turns a stored page into the name the explorer lists it by (its heading, "Parent › Child" for a page split
out of a bigger one); `with_display_titles` gives that name to manifests written when every split page was titled by its
slug. A fake MinIO stands in for the cluster."""
import asyncio
import os
import sys
from pathlib import Path

for name, value in {"REDIS_HOST": "localhost", "REDIS_PORT": "6379", "REDIS_PASSWORD": "x", "ENVIRONMENT": "local"}.items():
    os.environ.setdefault(name, value)
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "server"))

import domains

display_title = domains.dd.ingestion.post.domain.display_title
with_display_titles = domains.dd.ingestion.storage.service.with_display_titles
service = domains.dd.ingestion.storage.service


def check(label: str, got, want) -> None:
    assert got == want, f"{label}: expected {want!r}, got {got!r}"
    print(f"ok  {label}")


# ---- display_title --------------------------------------------------------------------------------------------------
check("whole page: its H1",
      display_title("# Orchestrate teams of Claude Code sessions\nSource: https://x/y\n\nCoordinate…", "slug"),
      "Orchestrate teams of Claude Code sessions")
check("a Source line right after the H1 never makes a child",
      display_title("# Hooks reference\nSource: https://x/hooks\n\n## Overview\ntext", "slug"),
      "Hooks reference")
check("sub-split child: Parent › Child",
      display_title("# Hooks reference\n\n### FileChanged\n\nRuns when a watched file changes", "slug"),
      "Hooks reference › FileChanged")
check("changelog release child reads its version",
      display_title('# Changelog\n\n<Update label="v3.2.3" description="2026-04-09">\n  notes', "slug"),
      "Changelog › v3.2.3")
check("throwaway filename-derived H1 gives way to the real one",
      display_title("# __init__\nSource: https://x/y\n\n# `fastmcp.apps`\nFastMCP Apps.", "slug"),
      "fastmcp.apps")
check("an index-letter H1 gives way to the real heading under it",
      display_title("# C\n\n## Cache Components\n\nA feature that enables caching.", "slug"), "Cache Components")
check("backticks and emphasis are dropped", display_title("# The `Agent` **class**\n\ntext", "slug"), "The Agent class")
check("the emoji in a real title is kept",
      display_title("# OCI IAM OAuth 🤝 FastMCP\nSource: https://x/oci\n", "slug"), "OCI IAM OAuth 🤝 FastMCP")
check("no heading: the first real sentence", display_title("Some prose that is long enough to be a title here.\n", "slug"),
      "Some prose that is long enough to be a title here.")
check("nothing usable: the fallback", display_title("", "the-slug"), "the-slug")
check("a child heading equal to its parent adds nothing", display_title("# Usage\n\n## Usage\n\ntext", "slug"), "Usage")
long = display_title("# " + "word " * 80 + "\n", "slug")
check("a title is bounded", len(long) <= 120, True)


# ---- with_display_titles --------------------------------------------------------------------------------------------
class FakeMinio:
    def __init__(self, pages: dict[str, str]):
        self.pages = pages
        self.reads = 0

    async def read_text(self, key: str) -> str:
        self.reads += 1
        if key not in self.pages:
            raise FileNotFoundError(key)
        return self.pages[key]


def manifest(entries: list[dict], run_id: str = "run-1") -> dict:
    return {"framework_slug": "demo", "run_id": run_id, "entries": entries}


def entry(idx: int, slug: str, title: str | None = None) -> dict:
    return {"idx": idx, "slug": slug, "key": f"ingestion/demo/pages/{idx:04d}-{slug}.md", "title": slug if title is None else title}


async def main() -> None:
    service._TITLE_CACHE.clear()
    minio = FakeMinio({
        "ingestion/demo/pages/0000-one.md": "# First page\nSource: https://x/1\n",
        "ingestion/demo/pages/0001-two.md": "# Second page\n\n### Part\n\ntext",
    })
    old = manifest([entry(0, "one"), entry(1, "two"), entry(2, "missing")])
    out = await with_display_titles(minio, "demo", old)
    check("slug-titled pages get their heading", [e["title"] for e in out["entries"]], ["First page", "Second page › Part", "missing"])
    check("the stored manifest is not changed", [e["title"] for e in old["entries"]], ["one", "two", "missing"])

    reads = minio.reads
    await with_display_titles(minio, "demo", old)
    check("the second request reads nothing (cached per run)", minio.reads, reads)

    titled = manifest([entry(0, "one", "Already titled")], run_id="run-2")
    check("an already-titled manifest is returned as it is", await with_display_titles(minio, "demo", titled) is titled, True)

    other_run = manifest([entry(0, "one")], run_id="run-3")
    minio.pages["ingestion/demo/pages/0000-one.md"] = "# Renamed after re-ingestion\n"
    out = await with_display_titles(minio, "demo", other_run)
    check("a new run is read afresh", out["entries"][0]["title"], "Renamed after re-ingestion")

    for n in range(40):
        await with_display_titles(minio, f"fw{n}", manifest([entry(0, "one")], run_id=f"r{n}"))
    check("the cache stays bounded", len(service._TITLE_CACHE) <= domains.dd.ingestion.storage.params.TITLE_CACHE_MAX, True)
    print("\nall checks passed")


asyncio.run(main())
