"""Offline check of Ingestion Tier 2 (`llms.txt` index → pages), the shared fetch it crawls with, and Tier 3's fall-through.

    cd apps/server && .venv/bin/python ../../scripts/server/test_ingestion_tier2.py

An in-process HTTP transport stands in for the documentation sites and in-memory fakes for Redis / MinIO. This pins what a run
does with a site that rate-limits, an index that is only a fraction of the docs, a single-heading index and a short page with
code — and that a Tier 3 with nothing to fetch hands over to the next tier instead of failing the run."""
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path

for name, value in {"REDIS_HOST": "localhost", "REDIS_PORT": "6379", "REDIS_PASSWORD": "x", "ENVIRONMENT": "local"}.items():
    os.environ.setdefault(name, value)
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "server"))

import httpx

import domains

ingestion = domains.dd.ingestion
tiers = ingestion.tiers
tier2, tier3, fetch = tiers.tier2, tiers.tier3, tiers.fetch
errors = tiers.errors


def check(label: str, got, want) -> None:
    assert got == want, f"{label}: expected {want!r}, got {got!r}"
    print(f"ok  {label}")


# ---- the shared fetch: what a server's headers say -------------------------------------------------------------------------
check("Retry-After in seconds", fetch.domain.retry_after_seconds("7", cap=30.0), 7.0)
check("Retry-After is capped by the caller", fetch.domain.retry_after_seconds("86400", cap=330.0), 330.0)
check("an HTTP-date Retry-After is ignored", fetch.domain.retry_after_seconds("Wed, 21 Oct 2026 07:28:00 GMT", cap=30.0), None)
check("no Retry-After", fetch.domain.retry_after_seconds(None, cap=30.0), None)
check("Hugging Face's rate-limit header: requests left, seconds to the reset",
      fetch.domain.rate_limit_window('"pages";r=99;t=109'), (99, 109.0))
check("…with nothing left", fetch.domain.rate_limit_window('"pages";r=0;t=128'), (0, 128.0))
check("the older spelling", fetch.domain.rate_limit_window("limit=100, remaining=3, reset=40"), (3, 40.0))
check("a header with only one of the two says nothing usable", fetch.domain.rate_limit_window('"pages";r=5'), None)
check("no header", fetch.domain.rate_limit_window(None), None)
check("backoff doubles each try, within jitter",
      all(0.5 * min(8, 2 ** (n - 1)) <= fetch.domain.backoff_seconds(n, initial=1.0, maximum=8.0) <= 1.5 * min(8, 2 ** (n - 1)) for n in range(1, 8)), True)
check("no wait configured, no wait", fetch.domain.backoff_seconds(3, initial=0.0, maximum=8.0), 0.0)

# ---- what an index is worth ------------------------------------------------------------------------------------------------
check("Qdrant's curated index (88 of 589) does not cover its site", tier2.domain.covers_site(88, 589), False)
check("a full index does", tier2.domain.covers_site(560, 589), True)
check("a quarter is enough", tier2.domain.covers_site(25, 100), True)
check("less than a quarter is not", tier2.domain.covers_site(24, 100), False)
check("a small site proves nothing (FastHTML: 21 of 41)", tier2.domain.covers_site(21, 41), True)
check("nor does a sitemap of every version and language (Streamlit: 344 of 5000)", tier2.domain.covers_site(344, 5000), True)

SHORT_PROSE = "# Overview\n\n" + "A short page of prose. " * 20
SHORT_CODE = "```python\nfrom fasthtml.common import *\napp = FastHTML(exts='ws')\n\n" + "# a websocket handler\n" * 15 + "```\n"
check("a short page of prose is a stub", (len(SHORT_PROSE.encode()) < 1024, tier2.domain.is_stub(SHORT_PROSE)), (True, True))
check("a short page that is a code example is not", (len(SHORT_CODE.encode()) < 1024, tier2.domain.is_stub(SHORT_CODE)), (True, False))
check("a long page is not", tier2.domain.is_stub("word " * 400), False)

links = [("A", "https://x/a", "Docs", ""), ("B", "https://x/b", "Docs", ""), ("C", "https://x/c", "Docs", "")]
check("one heading over every page is no section (Hugging Face's \"Docs\")",
      [section for _t, _u, section, _n in tier2.domain.drop_single_section(links)], ["", "", ""])
two = [("A", "https://x/a", "Learn", ""), ("B", "https://x/b", "API", ""), ("C", "https://x/c", "API", "")]
check("two real sections stay", tier2.domain.drop_single_section(two), two)
check("no headings stay none", [s for _t, _u, s, _n in tier2.domain.drop_single_section([("A", "u", "", "")])], [""])

check("a page fetched on its own that is short but holds code survives the post-ingest floor",
      [slug for slug, _u, _b in ingestion.post.domain.dedup_pages([("code", "u1", "```py\nx = 1\n```\n"), ("prose", "u2", "tiny prose")])[0]], ["code"])


check("a page's markdown twin: the trailing slash becomes `.md` (Qdrant)",
      tier3.domain.markdown_twin_url("https://qdrant.tech/documentation/search-patterns/"), "https://qdrant.tech/documentation/search-patterns.md")
check("…so does a bare path", tier3.domain.markdown_twin_url("https://x.dev/docs/intro"), "https://x.dev/docs/intro.md")
check("…and `.html` is swapped", tier3.domain.markdown_twin_url("https://x.dev/docs/intro.html"), "https://x.dev/docs/intro.md")
check("…the query and fragment go", tier3.domain.markdown_twin_url("https://x.dev/docs/intro/?v=2#top"), "https://x.dev/docs/intro.md")
check("a site's root has no twin", tier3.domain.markdown_twin_url("https://x.dev/"), None)
check("a markdown URL is its own twin", tier3.domain.markdown_twin_url("https://x.dev/docs/intro.md"), None)

LONG = "a-very-long-page-title-" * 6
check("a suffix is added within the slug limit, by cutting the base — never the suffix",
      (len(tier3.domain.suffixed_slug(LONG, "guide-2")), tier3.domain.suffixed_slug(LONG, "guide-2").endswith("-guide-2")), (tier3.params.SLUG_MAX_CHARS, True))
check("a short base is left whole", tier3.domain.suffixed_slug("intro", "guide"), "intro-guide")
check("two counters give two slugs even when the base fills the limit",
      tier3.domain.suffixed_slug(LONG, "x-2") != tier3.domain.suffixed_slug(LONG, "x-3"), True)

md_links = [("A", "https://x/a.md", "", ""), ("B", "https://x/b.md", "", ""), ("C", "https://x/c.md", "", ""), ("D", "https://x/d.txt", "", "")]
check("an index of markdown files serves markdown", tier2.domain.serves_markdown(md_links), True)
check("an index of web pages does not", tier2.domain.serves_markdown([("A", "https://x/a/", "", ""), ("B", "https://x/b/", "", "")]), False)
check("an empty index does not", tier2.domain.serves_markdown([]), False)


# ---- fakes ------------------------------------------------------------------------------------------------------------------
class FakeProgress:
    def __init__(self):
        self.calls: list[tuple] = []

    async def start(self, tier, total): self.calls.append(("start", tier, total))
    async def update_total(self, total): self.calls.append(("update_total", total))
    async def raise_if_cancelled(self): pass
    async def record_url(self, url, *, status, **rest): self.calls.append(("record_url", status, rest.get("error_msg")))
    async def update(self, current, last_url=""): self.calls.append(("update", current))
    async def finish(self, status="done", error=None): self.calls.append(("finish", status))
    async def close(self): pass

    def statuses(self) -> list[str]:
        return [c[1] for c in self.calls if c[0] == "finish"]

    def url_statuses(self) -> list[str]:
        return [c[1] for c in self.calls if c[0] == "record_url"]


class FakeStore:
    def __init__(self):
        self.pages: list[dict] = []
        self.index_summary = ""

    async def add_page(self, **kwargs): self.pages.append(kwargs)
    def reorder_by_url_list(self, urls): pass


def transport(handler):
    real = httpx.AsyncClient

    class Client(real):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    httpx.AsyncClient = Client
    return real


PAGE = "# A page\n\n" + "Documentation text for the reader. " * 60
MARKDOWN = {"content-type": "text/markdown"}


def index(*pairs: tuple[str, str], section: str = "Docs") -> str:
    return f"# Demo\n\n## {section}\n\n" + "".join(f"- [{title}](https://docs.example.com{path})\n" for title, path in pairs)


def sitemap(count: int) -> str:
    urls = "".join(f"<url><loc>https://docs.example.com/guide/p{n}</loc></url>" for n in range(count))
    return f'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'


async def run_tier2(handler, *, sitemap_url=None, path_filter=None):
    real = transport(handler)
    progress, store = FakeProgress(), FakeStore()
    try:
        outcome = await tier2.service.run(url="https://docs.example.com/llms.txt", framework_slug="demo", progress=progress, store=store,
                                          path_filter=path_filter, sitemap_url=sitemap_url)
        return outcome, progress, store, None
    except Exception as error:  # noqa: BLE001
        return None, progress, store, error
    finally:
        httpx.AsyncClient = real


async def run_tier3(handler, *, twins=False, **kwargs):
    real = transport(handler)
    progress, store = FakeProgress(), FakeStore()
    store.markdown_twins = twins
    try:
        outcome = await tier3.service.run(url="https://docs.example.com/sitemap.xml", framework_slug="demo", progress=progress, store=store, **kwargs)
        return outcome, progress, store, None
    except Exception as error:  # noqa: BLE001
        return None, progress, store, error
    finally:
        httpx.AsyncClient = real


async def main() -> None:
    fetch.params.CRAWL_WAIT_INITIAL_S = 0.0  # keep the retry tests fast

    # -- the shared fetch ----------------------------------------------------------------------------------------------------
    calls = {"n": 0}

    def flaky(request):
        calls["n"] += 1
        return httpx.Response(503, headers={"retry-after": "0"}) if calls["n"] < 3 else httpx.Response(200, text="ok")

    async def get(handler, **kw):
        real = transport(handler)
        try:
            async with httpx.AsyncClient() as client:
                return await fetch.service.get(client, "https://x/y", headers={}, attempts=kw.pop("attempts", 4), wait_initial=0.0, wait_max=0.0, retry_after_cap=kw.pop("cap", 30.0), **kw)
        finally:
            httpx.AsyncClient = real

    response = await get(flaky)
    check("a server that fails twice and answers is retried to success", (response.status_code, calls["n"]), (200, 3))

    calls["n"] = 0

    def down(request):
        calls["n"] += 1
        return httpx.Response(503)

    response = await get(down, attempts=3)
    check("a server that stays down is asked a bounded number of times and its last answer is returned", (response.status_code, calls["n"]), (503, 3))

    calls["n"] = 0

    def gone(request):
        calls["n"] += 1
        return httpx.Response(404)

    response = await get(gone)
    check("a 404 is final, not retried", (response.status_code, calls["n"]), (404, 1))

    calls["n"] = 0

    def unreachable(request):
        calls["n"] += 1
        raise httpx.ConnectError("no route", request=request)

    error = None
    try:
        await get(unreachable, attempts=3)
    except httpx.ConnectError as e:
        error = e
    check("a network error that outlasts the attempts is raised, after them", (type(error), calls["n"]), (httpx.ConnectError, 3))

    # a server that closes the connection without answering (Read the Docs did, once, to 1 of XGBoost's 92 pages) is the connection's fault
    calls["n"] = 0

    def hangs_up_once(request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.RemoteProtocolError("Server disconnected without sending a response.", request=request)
        return httpx.Response(200, text="ok")

    response = await get(hangs_up_once)
    check("a server that hangs up once and then answers is retried to success", (response.status_code, calls["n"]), (200, 2))

    # one pace for all of a crawl's requests: a request that starts during a hold waits instead of spending a 429
    state = {"until": 0.0, "n": 0}

    def limited(request):
        state["n"] += 1
        now = time.monotonic()
        if state["until"] == 0.0:
            state["until"] = now + 1.0
        return httpx.Response(429, headers={"retry-after": "1"}) if now < state["until"] else httpx.Response(200, text="ok")

    real = transport(limited)
    try:
        async with httpx.AsyncClient() as client:
            pacer = fetch.service.Pacer()

            async def one():
                return await fetch.service.get(client, "https://x/y", headers={}, attempts=4, wait_initial=0.0, wait_max=0.0, retry_after_cap=30.0, pacer=pacer)

            first = asyncio.create_task(one())
            await asyncio.sleep(0.15)  # the first has its 429 and the hold is in force
            rest = await asyncio.gather(*(one() for _ in range(4)))
            first_response = await first
    finally:
        httpx.AsyncClient = real
    check("every request got its answer", [r.status_code for r in [first_response, *rest]], [200] * 5)
    check("the four that started during the hold waited for it: 1 + 1 retry + 4 = 6 requests, not 10", state["n"], 6)

    # a window with nothing left is waited out before the next request spends a 429 on it
    window = {"n": 0, "stamps": []}

    def counted(request):
        window["n"] += 1
        window["stamps"].append(time.monotonic())
        return httpx.Response(200, text="ok", headers={"ratelimit": '"pages";r=0;t=1'} if window["n"] == 1 else {})

    real = transport(counted)
    try:
        async with httpx.AsyncClient() as client:
            pacer = fetch.service.Pacer()
            for _ in range(2):
                await fetch.service.get(client, "https://x/y", headers={}, attempts=2, wait_initial=0.0, wait_max=0.0, retry_after_cap=30.0, pacer=pacer)
    finally:
        httpx.AsyncClient = real
    check("after `r=0;t=1` the next request waits about a second", 0.9 <= window["stamps"][1] - window["stamps"][0] <= 2.5, True)

    pacer = fetch.service.Pacer(budget_s=0.5)
    check("a hold the waiting budget cannot cover is refused", (pacer.hold(0.2), pacer.hold(5.0)), (True, False))
    calls["n"] = 0

    def always_limited(request):
        calls["n"] += 1
        return httpx.Response(429, headers={"retry-after": "120"})

    real = transport(always_limited)
    began = time.monotonic()
    try:
        async with httpx.AsyncClient() as client:
            response = await fetch.service.get(client, "https://x/y", headers={}, attempts=5, wait_initial=0.0, wait_max=0.0, retry_after_cap=330.0, pacer=fetch.service.Pacer(budget_s=1.0))
    finally:
        httpx.AsyncClient = real
    check("with the budget spent the 429 comes back at once, no 120 s wait", (response.status_code, calls["n"], time.monotonic() - began < 2.0), (429, 1, True))

    waits: list[float] = []

    async def noted(seconds):
        waits.append(round(seconds))

    fetch.params.ANNOUNCE_MERGE_S = 0.1  # the real window is 2 s; shrunk so the test does not sleep through it
    pacer = fetch.service.Pacer(on_wait=noted)
    pacer.hold(0.2)
    pacer.hold(0.23)  # another request asking for the same pause a moment later
    await asyncio.gather(pacer.wait(), pacer.wait(), pacer.wait())
    check("a hold is announced once, however many requests wait for it or ask for it", len(waits), 1)
    pacer.hold(0.5)
    pacer.hold(0.1)  # shorter than the hold in force: changes nothing
    await pacer.wait()
    check("a hold that ends later than the merge window allows is a new pause: announced again", len(waits), 2)

    # -- Tier 2 --------------------------------------------------------------------------------------------------------------
    def site(overrides=None, *, pages=("/guide/intro.md", "/guide/setup.md", "/ref/api.md"), section="Docs", sitemap_text=None):
        text = index(*[(p.rsplit("/", 1)[-1], p) for p in pages], section=section)

        def handler(request):
            path = request.url.path
            if overrides and path in overrides:
                return overrides[path](request)
            if path == "/llms.txt":
                return httpx.Response(200, text=text)
            if path == "/sitemap.xml":
                return httpx.Response(200, text=sitemap_text) if sitemap_text is not None else httpx.Response(404)
            return httpx.Response(200, text=PAGE, headers=MARKDOWN)

        return handler

    outcome, progress, store, error = await run_tier2(site())
    check("a good index is fetched page by page", (outcome, error, len(store.pages)), (3, None, 3))
    check("the tier never marks the run done (the dispatcher does)", progress.statuses(), [])
    check("a single `##` heading is no section: the pages carry none", {p["section"] for p in store.pages}, {""})

    handler = site()
    real_index = index(("Intro", "/guide/intro.md"), ("API", "/ref/api.md"), section="Guides").replace("- [API]", "## Reference\n\n- [API]")
    outcome, progress, store, error = await run_tier2(lambda r: httpx.Response(200, text=real_index) if r.url.path == "/llms.txt" else handler(r))
    check("two real sections are kept", sorted(p["section"] for p in store.pages), ["Guides", "Reference"])

    # a page the site rate-limits once is not lost
    seen = {"n": 0}

    def once_limited(request):
        seen["n"] += 1
        return httpx.Response(429, headers={"retry-after": "1"}) if seen["n"] == 1 else httpx.Response(200, text=PAGE, headers=MARKDOWN)

    outcome, progress, store, error = await run_tier2(site({"/guide/setup.md": once_limited}))
    check("a page that is rate-limited once is retried after the pause, not dropped", (len(store.pages), error), (3, None))
    check("…and the pause is on the URL log", "rate_limited" in progress.url_statuses(), True)

    def forbidden(request):
        return httpx.Response(403)

    outcome, progress, store, error = await run_tier2(site({"/guide/setup.md": forbidden}))
    check("a 403 page is dropped (final), the rest are kept", (len(store.pages), "http_error" in progress.url_statuses()), (2, True))

    # short pages: prose is a stub, a code example is not
    def short_pages(request):
        body = SHORT_CODE if request.url.path.endswith("setup.md") else SHORT_PROSE if request.url.path.endswith("api.md") else PAGE
        return httpx.Response(200, text=body, headers=MARKDOWN)

    outcome, progress, store, error = await run_tier2(site({"/guide/setup.md": short_pages, "/ref/api.md": short_pages}))
    check("a short page of prose is dropped, a short code example is kept", sorted(p["url"].rsplit("/", 1)[-1] for p in store.pages), ["intro.md", "setup.md"])
    check("…and the drop is logged", "stub_dropped" in progress.url_statuses(), True)

    # an index that is a fraction of the site
    outcome, progress, store, error = await run_tier2(site(sitemap_text=sitemap(200)), sitemap_url="https://docs.example.com/sitemap.xml")
    check("an index listing 3 of 200 pages falls through to the sitemap", (type(error), progress.statuses(), len(store.pages)), (errors.TierUnavailable, ["downgrade"], 0))
    check("…and says why in the URL log", any("falling through to the sitemap" in (c[2] or "") for c in progress.calls if c[0] == "record_url"), True)

    outcome, progress, store, error = await run_tier2(site(sitemap_text=sitemap(40)), sitemap_url="https://docs.example.com/sitemap.xml")
    check("a small site is not judged (3 of 40)", (outcome, error), (3, None))

    pages = tuple((f"/guide/p{n}.md") for n in range(60))
    outcome, progress, store, error = await run_tier2(site(pages=pages, sitemap_text=sitemap(120)), sitemap_url="https://docs.example.com/sitemap.xml")
    check("an index with half the site's pages is fine", (outcome, error), (60, None))

    outcome, progress, store, error = await run_tier2(site(sitemap_text=None), sitemap_url="https://docs.example.com/sitemap.xml")
    check("a missing sitemap is no reason to doubt the index", (outcome, error), (3, None))

    outcome, progress, store, error = await run_tier2(site(sitemap_text=sitemap(200)))
    check("with no sitemap declared there is nothing to compare with", (outcome, error), (3, None))

    outcome, progress, store, error = await run_tier2(site(sitemap_text=sitemap(200)), sitemap_url="https://docs.example.com/sitemap.xml", path_filter={"include": ["/other/"]})
    check("the catalog's path filter counts in both lists: nothing left in the index → TierUnavailable",
          (type(error), progress.statuses()), (errors.TierUnavailable, ["downgrade"]))

    # -- Tier 3: nothing to fetch hands over -----------------------------------------------------------------------------------
    outcome, progress, store, error = await run_tier3(lambda r: httpx.Response(200, text="<urlset></urlset>"))
    check("an empty sitemap is TierUnavailable, handed on as a downgrade — not a failed run", (type(error), progress.statuses(), len(store.pages)), (errors.TierUnavailable, ["downgrade"], 0))
    outcome, progress, store, error = await run_tier3(lambda r: httpx.Response(404))
    check("so is a sitemap that is not there", (type(error), progress.statuses()), (errors.TierUnavailable, ["downgrade"]))
    outcome, progress, store, error = await run_tier3(lambda r: httpx.Response(200, text=sitemap(5)), path_filter={"include": ["/other/"]})
    check("so is one whose URLs are all outside the catalog's filter", (type(error), progress.statuses()), (errors.TierUnavailable, ["downgrade"]))

    def sitemap_site(request):
        return httpx.Response(200, text=sitemap(3)) if request.url.path == "/sitemap.xml" else httpx.Response(200, text="<html><body><main><h1>Page</h1><p>" + "text " * 100 + "</p></main></body></html>")

    outcome, progress, store, error = await run_tier3(sitemap_site)
    check("a sitemap with pages still works", (outcome, error, len(store.pages)), (3, None, 3))

    # markdown twins: asked for first when Tier 2 handed the site over, the HTML only when a page has none
    asked: list[str] = []
    HTML_PAGE = "<html><body><main><h1>Scraped</h1><p>" + "text " * 100 + "</p></main></body></html>"
    TWIN = "# From the twin\n\n" + "Clean markdown text. " * 40

    def twin_site(request):
        path = request.url.path
        asked.append(path)
        if path == "/sitemap.xml":
            return httpx.Response(200, text=sitemap(3))
        if path == "/guide/p0.md":
            return httpx.Response(200, text=TWIN, headers=MARKDOWN)
        if path == "/guide/p1.md":
            return httpx.Response(404, text="<html>not found</html>")
        if path == "/guide/p2.md":
            return httpx.Response(200, text="<!doctype html><html><body>" + "catch-all " * 60 + "</body></html>")
        return httpx.Response(200, text=HTML_PAGE)

    outcome, progress, store, error = await run_tier3(twin_site, twins=True)
    by_url = {p["url"].rsplit("/", 1)[-1]: p for p in store.pages}
    check("with twins on, a page that has one is taken as markdown, titled by its own heading", (by_url["p0"]["title"], by_url["p0"]["body"] == TWIN), ("From the twin", True))
    check("…and its HTML is never asked for", "/guide/p0" in asked, False)
    check("a page with no twin falls back to its HTML", by_url["p1"]["title"], "Scraped")
    check("a twin that is really an HTML page (a site answering 200 to anything) is no twin", by_url["p2"]["title"], "Scraped")
    asked.clear()
    outcome, progress, store, error = await run_tier3(twin_site)
    by_url = {p["url"].rsplit("/", 1)[-1]: p for p in store.pages}
    check("with twins off, a site where only one of three pages has a twin is scraped, after one probe of each page",
          (error, sorted(p for p in asked if p.endswith(".md")), by_url["p0"]["title"]), (None, ["/guide/p0.md", "/guide/p1.md", "/guide/p2.md"], "Scraped"))

    # a site that publishes twins is found by asking a few pages, without Tier 2 having said so
    TWIN_OF = {f"/guide/p{i}.md": f"# Twin {i}\n\n" + "Clean markdown text. " * 40 for i in range(3)}
    asked.clear()

    def all_twins(request):
        path = request.url.path
        asked.append(path)
        if path == "/sitemap.xml":
            return httpx.Response(200, text=sitemap(3))
        if path in TWIN_OF:
            return httpx.Response(200, text=TWIN_OF[path], headers=MARKDOWN)
        return httpx.Response(200, text=HTML_PAGE)

    outcome, progress, store, error = await run_tier3(all_twins)
    check("a site whose pages all have twins is read from them, whatever Tier 2 said", (error, sorted(p["title"] for p in store.pages)), (None, ["Twin 0", "Twin 1", "Twin 2"]))
    check("…and none of its HTML pages is asked for", [p for p in asked if p.startswith("/guide/") and not p.endswith(".md")], [])
    real_client = httpx.AsyncClient

    def any_twin(request):
        return httpx.Response(200, text="# Twin\n\n" + "Clean markdown text. " * 40, headers=MARKDOWN)

    async with real_client(transport=httpx.MockTransport(any_twin)) as client:
        urls = [f"https://docs.example.com/guide/p{i}" for i in range(12)]
        check("the probe finds twins on a site that publishes them", await tier3.service.site_serves_twins(client, urls), True)
        check("…nothing to probe in a site of two pages", await tier3.service.site_serves_twins(client, urls[:2]), False)
    async with real_client(transport=httpx.MockTransport(twin_site)) as client:
        check("…and a site where one page in three has a twin does not pass", await tier3.service.site_serves_twins(client, ["https://x/guide/p0", "https://x/guide/p1", "https://x/guide/p2"]), False)

    # pages that share one long title used to spin the slug-collision loop for ever (the counter was cut off the slug)
    long_title = "How to choose an embedding model for your search application and evaluate the tradeoffs " * 2

    def long_titled(request):
        if request.url.path == "/sitemap.xml":
            return httpx.Response(200, text=sitemap(3))
        return httpx.Response(200, text=f"# {long_title}\n\n" + "Clean markdown text. " * 40, headers=MARKDOWN)

    outcome, progress, store, error = await asyncio.wait_for(run_tier3(long_titled, twins=True), 20.0)
    slugs = [p["slug"] for p in store.pages]
    check("three pages with the same 170-character title get three distinct slugs, within the limit, and the run ends",
          (error, len(set(slugs)), max(len(s) for s in slugs) <= tier3.params.SLUG_MAX_CHARS), (None, 3, True))

    long_index = "# Demo\n\n## Docs\n\n" + "".join(f"- [{long_title}](https://docs.example.com/guide/p{n}.md)\n" for n in range(3))
    outcome, progress, store, error = await asyncio.wait_for(run_tier2(
        lambda r: httpx.Response(200, text=long_index) if r.url.path == "/llms.txt" else httpx.Response(200, text=PAGE, headers=MARKDOWN)), 20.0)
    slugs = [p["slug"] for p in store.pages]
    check("so do three index entries with the same long title (Tier 2 had the same loop)",
          (error, len(set(slugs)), max(len(s) for s in slugs) <= 80), (None, 3, True))

    # Tier 2 turns twins on when it hands over an index of markdown files, not for an index of web pages
    outcome, progress, store, error = await run_tier2(site(sitemap_text=sitemap(200)), sitemap_url="https://docs.example.com/sitemap.xml")
    check("an index of markdown files that falls through turns the sitemap tier's twins on", (type(error), store.markdown_twins), (errors.TierUnavailable, True))
    web_pages = site(pages=("/guide/a", "/guide/b", "/guide/c"), sitemap_text=sitemap(200))
    outcome, progress, store, error = await run_tier2(web_pages, sitemap_url="https://docs.example.com/sitemap.xml")
    check("an index of web pages does not", (type(error), getattr(store, "markdown_twins", False)), (errors.TierUnavailable, False))

    real = transport(lambda r: httpx.Response(200, text=sitemap(4) + "") if r.url.path == "/sitemap.xml" else httpx.Response(404))
    try:
        async with httpx.AsyncClient() as client:
            scoped = await tier3.service.scoped_urls(client, "https://docs.example.com/sitemap.xml", {"exclude": ["/guide/p2"]})
    finally:
        httpx.AsyncClient = real
    check("the sitemap URLs a Tier 3 run would fetch, filtered as it filters them", [u.rsplit("/", 1)[-1] for u in scoped], ["p0", "p1", "p3"])

    # -- the dispatcher walks from a Tier 2 that gives up to the next tier --------------------------------------------------------
    walked: list[str] = []

    async def run_tier(kind, url, progress):
        walked.append(kind)
        if kind == "llms_txt":
            raise errors.TierUnavailable("the index covers 15% of the sitemap")

    fresh = FakeProgress()
    progress = await ingestion.runtime.dispatch.service.run_source_chain(
        [{"kind": "llms_txt", "url": "u"}, {"kind": "sitemap", "url": "v"}], FakeProgress(),
        new_progress=lambda: fresh, run_tier=run_tier, base_result={}, slug="demo")
    check("Tier 2 handing over runs the sitemap next, with a fresh progress", (walked, progress is fresh), (["llms_txt", "sitemap"], True))

    # -- storage: a stored image keeps this app's own path; a page is read by its idx -------------------------------------
    class FakeMinio:
        def __init__(self):
            self.objects: dict = {}

        async def write(self, key, data, content_type=""): self.objects[key] = data
        async def delete(self, key): self.objects.pop(key, None)
        async def read_text(self, key):
            value = self.objects[key]
            return value if isinstance(value, str) else value.decode()
        async def write_many(self, items):
            for key, text, _ctype in items:
                self.objects[key] = text

    class FakeRedis:
        async def set(self, *args, **kwargs): pass

    png = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 12

    def image_site(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=png, headers={"content-type": "image/png"})

    minio = FakeMinio()
    store = ingestion.storage.service.Store("run-1", "demo", FakeRedis(), minio)
    store._artifact_client = httpx.AsyncClient(transport=httpx.MockTransport(image_site))
    page = "# Guide\n\n![chart](/images/chart.png)\n\nSee the [setup](/docs/setup) and ![logo](https://huggingface.co/logo.png).\n" + "text. " * 60
    entry = await store.add_page(slug="guide", url="https://huggingface.co/docs/demo/guide.md", body=page, tier="llms_txt")
    stored = minio.objects[entry.key]
    images = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", stored)
    check("both images are stored as this app's artifacts, on this app's path",
          (len(images), all(url.startswith("/api/v1/dd/ingestion/demo/artifacts/") for url in images)), (2, True))
    check("…none of them was made a path on the docs' host", "https://huggingface.co/api/" in stored, False)
    check("…while a page link is still made absolute", "[setup](https://huggingface.co/docs/setup)" in stored, True)
    await store._artifact_client.aclose()

    storage = ingestion.storage.service
    # Tiers 2-4 store the manifest in the site's navigation order; `idx` is the order the pages were written in.
    manifest = {"entries": [
        {"idx": 2, "slug": "quick-start", "key": "ingestion/demo/pages/0002-quick-start.md"},
        {"idx": 0, "slug": "setup", "key": "ingestion/demo/pages/0000-setup.md"},
        {"idx": 1, "slug": "tutorial", "key": "ingestion/demo/pages/0001-tutorial.md"},
    ]}
    minio.objects[ingestion.storage.keys.manifest_key("demo")] = json.dumps(manifest)
    for item in manifest["entries"]:
        minio.objects[item["key"]] = f"# {item['slug']}\n"
    check("a page is read by its idx, not by its place in a manifest that is in navigation order",
          [await storage.read_framework_page(minio, "demo", n) for n in (2, 0, 1)], ["# quick-start\n", "# setup\n", "# tutorial\n"])
    check("an idx no page has is not found", [await storage.read_framework_page(minio, "demo", n) for n in (3, -1)], [None, None])
    check("a manifest whose entries carry no idx is read by position", ingestion.storage.domain.entry_with_idx([{"key": "a"}, {"key": "b"}], 1), {"key": "b"})


asyncio.run(main())
print("\nall checks passed")
