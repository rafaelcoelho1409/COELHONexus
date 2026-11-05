"""Offline check of what tiers 2-5 keep of what they fetch (`ingestion/raw/{slug}/`): the index, every response as it came (HTML and
sitemaps gzipped), a note of the requests that gave nothing, the decisions taken from the network, and `capture.json` last.

    cd apps/server && .venv/bin/python ../../scripts/server/test_ingestion_capture.py

An in-process HTTP transport stands in for the sites, in-memory fakes for MinIO. Tier 1's capture is pinned in `test_ingestion_tier1.py`;
the Playwright phase of Tier 4 needs a browser, so only what it hands the capture is checked."""
import asyncio
import gzip
import json
import os
import sys
from pathlib import Path

for name, value in {"REDIS_HOST": "localhost", "REDIS_PORT": "6379", "REDIS_PASSWORD": "x", "ENVIRONMENT": "local"}.items():
    os.environ.setdefault(name, value)
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "server"))

import httpx
from capture_fakes import CaptureMinio

import domains

ingestion = domains.dd.ingestion
capture = ingestion.capture
tiers = ingestion.tiers
errors = tiers.errors


def check(label: str, got, want) -> None:
    assert got == want, f"{label}: expected {want!r}, got {got!r}"
    print(f"ok  {label}")


class FakeProgress:
    async def start(self, tier, total): pass
    async def update_total(self, total): pass
    async def raise_if_cancelled(self): pass
    async def record_url(self, url, *, status, **rest): pass
    async def update(self, current, last_url=""): pass
    async def finish(self, status="done", error=None): pass
    async def close(self): pass


class FakeStore:
    framework_slug = "demo"

    def __init__(self):
        self.pages: list[dict] = []
        self.index_summary = ""
        self.markdown_twins = False
        self.minio = CaptureMinio()

    async def add_page(self, **kwargs): self.pages.append(kwargs)
    def reorder_by_url_list(self, urls): pass


def prose(topic: str, n: int = 1400) -> str:
    return (f"{topic} is described here in some detail. " * 60)[:n]


def serving(handler):
    """Make every `httpx.AsyncClient` in this process answer through `handler`; returns what to put back."""
    real = httpx.AsyncClient

    class Client(real):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    httpx.AsyncClient = Client
    return real


def names(cap) -> dict[str, "capture.entities.CapturedFile"]:
    return {f.url: f for f in cap.files}


async def main() -> None:
    tiers.fetch.params.CRAWL_WAIT_INITIAL_S = 0.0

    # ---- Tier 2: llms.txt index + the pages it lists --------------------------------------------------------------------------
    index = "# Example\n\n> Summary.\n\n## Guides\n\n- [Intro](https://docs.example.com/intro.md): start\n- [Setup](https://docs.example.com/setup.html)\n- [Gone](https://docs.example.com/gone.md)\n"
    html_page = f"<html><head><title>Setup</title></head><body><main><h1>Setup</h1><p>{prose('Setup')}</p><pre>pip install x</pre></main></body></html>"

    def site2(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/llms.txt":
            return httpx.Response(200, text=index, headers={"content-type": "text/plain"})
        if path == "/intro.md":
            return httpx.Response(200, text="# Intro\n\n" + prose("Intro"), headers={"content-type": "text/markdown"})
        if path == "/setup.html":
            return httpx.Response(200, text=html_page, headers={"content-type": "text/html; charset=utf-8"})
        return httpx.Response(404)

    real = serving(site2)
    try:
        store = FakeStore()
        written = await tiers.tier2.service.run(url="https://docs.example.com/llms.txt", framework_slug="demo", progress=FakeProgress(), store=store)
        check("Tier 2 writes the two pages that are there", written, 2)
        cap = await capture.service.load(store.minio, "demo")
        by_url = names(cap)
        check("its capture holds the index and a file (or a note) for every request", sorted(by_url), [
            "https://docs.example.com/gone.md", "https://docs.example.com/intro.md", "https://docs.example.com/llms.txt", "https://docs.example.com/setup.html"])
        check("…the index as the site sent it", await capture.service.read_text(store.minio, cap, "llms.txt"), index)
        check("…a 404 is a note of the request, not a file", (by_url["https://docs.example.com/gone.md"].status, by_url["https://docs.example.com/gone.md"].stored), (404, False))
        setup = by_url["https://docs.example.com/setup.html"]
        check("…HTML is kept gzipped, and reads back exactly as it came", (setup.encoding, setup.name.endswith(".html"), await capture.service.read_text(store.minio, cap, setup.name)), ("gzip", True, html_page))
        stored = store.minio.objects[ingestion.storage.keys.raw_file_key("demo", setup.name + ".gz")]
        check("…the object really is gzip", (stored[:2], gzip.decompress(stored).decode() == html_page), (b"\x1f\x8b", True))
        intro = by_url["https://docs.example.com/intro.md"]
        check("…Markdown is kept as text, named by its URL", (intro.encoding, intro.name == capture.domain.page_file_name(intro.url, "text/markdown"), intro.name.endswith(".md")), ("", True, True))
        check("…the catalog's settings ride along for the transform", {k: cap.params[k] for k in ("path_filter", "code_language", "sitemap_url")}, {"path_filter": None, "code_language": "", "sitemap_url": None})
        check("the old raw copy is no longer kept next to the pages", [p["keep_raw"] for p in store.pages], [False, False])
        check("nothing but the capture is under the raw prefix", sorted({k.split("/")[3] for k in store.minio.objects}), ["capture.json", "files"])

        def broken(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404)

        httpx.AsyncClient = real
        real = serving(broken)
        store = FakeStore()
        try:
            await tiers.tier2.service.run(url="https://docs.example.com/llms.txt", framework_slug="demo", progress=FakeProgress(), store=store)
            error = None
        except errors.TierUnavailable as e:
            error = e
        check("a tier that falls through leaves no capture behind", (type(error), store.minio.objects), (errors.TierUnavailable, {}))
    finally:
        httpx.AsyncClient = real

    # ---- Tier 3: sitemap + pages, markdown twins first ------------------------------------------------------------------------
    sitemap = ('<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
               '<url><loc>https://docs.example.com/docs/a</loc><lastmod>2026-01-02</lastmod></url>'
               '<url><loc>https://docs.example.com/docs/b</loc></url></urlset>')
    page_b = f"<html><head><title>B</title></head><body><main><h1>B</h1><p>{prose('B')}</p></main></body></html>"

    def site3(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/sitemap.xml":
            return httpx.Response(200, text=sitemap, headers={"content-type": "application/xml"})
        if path == "/docs/a.md":
            return httpx.Response(200, text="# A\n\n" + prose("A") + "\n\n```py\nx = 1\n```\n", headers={"content-type": "text/markdown"})
        if path == "/docs/b":
            return httpx.Response(200, text=page_b, headers={"content-type": "text/html"})
        return httpx.Response(404)

    real = serving(site3)
    try:
        store = FakeStore()
        store.markdown_twins = True
        written = await tiers.tier3.service.run(url="https://docs.example.com/sitemap.xml", framework_slug="demo", progress=FakeProgress(), store=store)
        cap = await capture.service.load(store.minio, "demo")
        check("Tier 3 writes both pages", written, 2)
        by_url = names(cap)
        twin = by_url["https://docs.example.com/docs/a.md"]
        check("a page's markdown twin is kept as it came and says which page it stands for", (twin.for_url, twin.via, twin.stored), ("https://docs.example.com/docs/a", "twin", True))
        check("…a page with no twin has the twin's 404 noted, and its HTML kept",
              (by_url["https://docs.example.com/docs/b.md"].stored, by_url["https://docs.example.com/docs/b.md"].status, by_url["https://docs.example.com/docs/b"].encoding), (False, 404, "gzip"))
        check("…and the kept response that stands for each page is found by its URL",
              (capture.domain.file_for_url(cap, "https://docs.example.com/docs/a").name == twin.name, capture.domain.file_for_url(cap, "https://docs.example.com/docs/b").via), (True, "http"))
        check("the sitemap is kept as the site sent it, as an index", (await capture.service.read_text(store.minio, cap, by_url["https://docs.example.com/sitemap.xml"].name), by_url["https://docs.example.com/sitemap.xml"].role), (sitemap, "index"))
        check("what the run decided from the network rides along: the pages in order, the sitemap's dates, whether twins were asked for",
              (cap.params["urls"], cap.params["lastmod"], cap.params["twins"]), (["https://docs.example.com/docs/a", "https://docs.example.com/docs/b"], {"https://docs.example.com/docs/a": "2026-01-02"}, True))
        check("the old raw copy is no longer kept next to the pages", [p["keep_raw"] for p in store.pages], [False, False])
    finally:
        httpx.AsyncClient = real

    # ---- Tier 4: a docs site, page by page ---------------------------------------------------------------------------------------
    def site4(request: httpx.Request) -> httpx.Response:
        if request.url.path in ("/manual/index.html", "/manual/a.html"):
            return httpx.Response(200, text=f"<html><head><title>P</title></head><body><main><h1>P</h1><p>{prose('P', 1500)}</p></main></body></html>", headers={"content-type": "text/html"})
        return httpx.Response(404)

    async def seeder(host, path): return ["https://docs.example.org/manual/index.html", "https://docs.example.org/manual/a.html", "https://docs.example.org/manual/b.html"]
    async def enrichment(url, client): return []
    async def spa(candidates, client): return False
    async def bfs(seeds, **kwargs): return seeds
    async def browser(urls, **kwargs): return 0

    t4 = tiers.tier4.service
    saved = (t4._seeder_discover, t4._seed_enrichment, t4._is_spa_majority, t4._bfs, t4._phase4b_playwright)
    t4._seeder_discover, t4._seed_enrichment, t4._is_spa_majority, t4._bfs, t4._phase4b_playwright = seeder, enrichment, spa, bfs, browser
    real = serving(site4)
    try:
        store = FakeStore()
        written = await t4.run(url="https://docs.example.org/manual/index.html", framework_slug="demo", progress=FakeProgress(), store=store, code_language="bash")
        cap = await capture.service.load(store.minio, "demo")
        by_url = names(cap)
        check("Tier 4 writes the pages it could read", written, 2)
        check("its capture has each page's HTML (gzipped) and a note of the one that was not there",
              ([by_url[u].encoding for u in sorted(by_url) if by_url[u].stored], by_url["https://docs.example.org/manual/b.html"].status), (["gzip", "gzip"], 404))
        check("the decisions from discovery ride along: the pages crawled, in order, their anchors, and whether the browser was needed",
              (cap.params["urls"], cap.params["anchors"], cap.params["spa"], cap.params["code_language"]),
              (["https://docs.example.org/manual/index.html", "https://docs.example.org/manual/a.html", "https://docs.example.org/manual/b.html"], {}, False, "bash"))
        check("the old raw copy is no longer kept next to the pages", [p["keep_raw"] for p in store.pages], [False, False])

        writer = capture.service.Writer(store.minio, "demo", "docs", "https://docs.example.org/", capture_params={})
        await t4._keep_rendering(writer, "https://docs.example.org/manual/a.html", "<html>rendered</html>")
        await t4._keep_rendering(writer, "https://docs.example.org/manual/c.html", None, error="Timeout")
        rendered, failed = sorted(writer.files, key=lambda f: f.url)
        check("what the browser rendered is kept beside (never over) the page a plain request got, and a failure is noted",
              (rendered.name.endswith(".browser.html"), rendered.via, rendered.encoding, failed.stored, failed.error, rendered.name != capture.domain.page_file_name(rendered.url, "text/html")),
              (True, "browser", "gzip", False, "Timeout", True))
    finally:
        t4._seeder_discover, t4._seed_enrichment, t4._is_spa_majority, t4._bfs, t4._phase4b_playwright = saved
        httpx.AsyncClient = real

    # ---- Tier 5: a repository's docs -------------------------------------------------------------------------------------------------
    tree = {"tree": [{"path": "docs/a.md", "type": "blob", "size": 1500}, {"path": "src/x.py", "type": "blob", "size": 90}]}
    readme = "# A\n\n" + prose("A")

    def site5(request: httpx.Request) -> httpx.Response:
        if request.url.host == "raw.githubusercontent.com":
            return httpx.Response(200, text=readme)
        if "git/trees" in request.url.path:
            return httpx.Response(200, json=tree)
        return httpx.Response(200, json={"default_branch": "main"})

    real = serving(site5)
    try:
        store = FakeStore()
        written = await tiers.tier5.service.run(url="https://github.com/acme/widgets", framework_slug="demo", progress=FakeProgress(), store=store)
        cap = await capture.service.load(store.minio, "demo")
        check("Tier 5 writes the docs file", written, 1)
        check("its capture holds the tree as the API answered, the file as the repository has it, and which files were chosen",
              (json.loads(await capture.service.read_text(store.minio, cap, "tree.json")) == tree,
               await capture.service.read_text(store.minio, cap, capture.domain.page_file_name("https://raw.githubusercontent.com/acme/widgets/main/docs/a.md")),
               {k: cap.params[k] for k in ("org", "repo", "branch", "paths")}),
              (True, readme, {"org": "acme", "repo": "widgets", "branch": "main", "paths": ["docs/a.md"]}))
        check("the old raw copy is no longer kept next to the pages", [p["keep_raw"] for p in store.pages], [False])
    finally:
        httpx.AsyncClient = real

    print("\nall checks passed")


asyncio.run(main())
