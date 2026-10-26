"""Offline check of the Ingestion polish: pages in the site's own order, short real pages kept, repeated titles told
apart, relative links made absolute, and the normalizer's `<video>` / callout-fence clean-up.

    cd apps/server && .venv/bin/python ../../scripts/server/test_ingestion_polish.py

Pure functions are checked directly; the split → order path runs through `post.service.apply_to_store` against a fake store,
and Tier 1's fetch of the site's index through an in-process HTTP transport."""
import asyncio
import os
import sys
from pathlib import Path

for name, value in {"REDIS_HOST": "localhost", "REDIS_PORT": "6379", "REDIS_PASSWORD": "x", "ENVIRONMENT": "local"}.items():
    os.environ.setdefault(name, value)
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "server"))

import httpx  # noqa: E402

import domains  # noqa: E402

ingestion = domains.dd.ingestion
post = ingestion.post
tier1 = ingestion.tiers.tier1
normalize = domains.dd.synth.nodes.corpus_normalize


def check(label: str, got, want) -> None:
    assert got == want, f"{label}: expected {want!r}, got {got!r}"
    print(f"ok  {label}")


# ---- order_by_nav ---------------------------------------------------------------------------------------------------
def w(slug: str, source: str) -> tuple[str, str, str]:
    return (slug, f"# {slug}\n", source)


alphabetical = [w("advanced", "https://s/docs/advanced"), w("intro", "https://s/docs/intro"), w("quickstart", "https://s/docs/quickstart")]
nav = ["https://s/docs/intro.md", "https://s/docs/quickstart.md", "https://s/docs/advanced.md"]
check("pages follow the index (a `.md` suffix in the index is no obstacle)",
      [s for s, _b, _p in post.domain.order_by_nav(alphabetical, nav)], ["intro", "quickstart", "advanced"])
check("no index: untouched", post.domain.order_by_nav(alphabetical, []), alphabetical)

with_children = [w("a", "https://s/a"), w("a-sub1", "https://s/a"), w("a-sub2", "https://s/a"), w("b", "https://s/b")]
check("pages that share a URL (a sub-split) stay together, in order",
      [s for s, _b, _p in post.domain.order_by_nav(with_children, ["https://s/b", "https://s/a"])], ["b", "a", "a-sub1", "a-sub2"])

mixed = [w("x", "https://s/x"), w("unlisted", "https://s/zzz"), w("y", "https://s/y")]
check("pages the index does not list keep their order, after the listed ones",
      [s for s, _b, _p in post.domain.order_by_nav(mixed, ["https://s/y", "https://s/x"])], ["y", "x", "unlisted"])

unrelated = [w(f"p{n}", f"https://s/p{n}") for n in range(6)]
check("an index that names under half the pages is not this site's navigation: untouched",
      post.domain.order_by_nav(unrelated, ["https://s/p5", "https://s/p4"]), unrelated)
check("pages with no source URL cannot be placed: a bundle without markers is untouched",
      post.domain.order_by_nav([w("a", ""), w("b", "")], ["https://s/b"]), [w("a", ""), w("b", "")])


# ---- disambiguate_titles --------------------------------------------------------------------------------------------
BASE = "https://docs.browser-use.com/open-source"
check("repeated titles are told apart by their section",
      post.domain.disambiguate_titles([
          ("All Parameters", f"{BASE}/customize/agent/all-parameters"),
          ("Configuration", f"{BASE}/customize/agent/basics"),
          ("All Parameters", f"{BASE}/customize/browser/all-parameters"),
          ("Configuration", f"{BASE}/customize/browser/basics"),
          ("Quickstart", f"{BASE}/quickstart"),
      ]),
      ["Agent › All Parameters", "Agent › Configuration", "Browser › All Parameters", "Browser › Configuration", "Quickstart"])
check("two folders deep when one is not enough",
      post.domain.disambiguate_titles([
          ("All Parameters", f"{BASE}/customize/agent/all-parameters"),
          ("All Parameters", f"{BASE}/legacy/agent/all-parameters"),
      ]),
      ["Customize › Agent › All Parameters", "Legacy › Agent › All Parameters"])
check("unique titles are untouched", post.domain.disambiguate_titles([("A", "https://s/x/a"), ("B", "https://s/y/b")]), ["A", "B"])
check("repeats in one folder are told apart by their file names, minus the title they end with",
      post.domain.disambiguate_titles([
          ("Overview", "https://s/docs/platform/dashboard_overview"),
          ("Overview", "https://s/docs/platform/datasets_overview"),
          ("Overview", "https://s/docs/library/evaluations_overview"),
      ]),
      ["Dashboard › Overview", "Datasets › Overview", "Evaluations › Overview"])
check("a file name that does not end with the title is used whole",
      post.domain.disambiguate_titles([("Data Drift", "https://s/metrics/explainer_drift"), ("Data Drift", "https://s/metrics/preset_data_drift.md")]),
      ["Explainer Drift › Data Drift", "Preset › Data Drift"])
check("repeats on the very same URL (a changelog's sub-pages) have nothing to be told apart by: left as they were",
      post.domain.disambiguate_titles([("New Contributors", "https://s/changelog"), ("New Contributors", "https://s/changelog")]),
      ["New Contributors", "New Contributors"])
check("the comparison ignores case", post.domain.disambiguate_titles([("Overview", "https://s/tools/o"), ("overview", "https://s/skills/o")]),
      ["Tools › Overview", "Skills › overview"])
check("the language and the word `docs` never name a page (Claude Code's pages are all /docs/en/…)",
      post.domain.disambiguate_titles([
          ("Quickstart", "https://code.claude.com/docs/en/quickstart"),
          ("Quickstart", "https://code.claude.com/docs/en/agent-sdk/quickstart"),
      ]),
      ["Quickstart", "Agent Sdk › Quickstart"])
check("a title written as a markdown link reads as its text",
      post.domain.display_title("# Changelog\n\n## [v2.9.1: Call Me Maybe](https://github.com/x/y/releases/tag/v2.9.1)\n\ntext", "slug"),
      "Changelog › v2.9.1: Call Me Maybe")
check("…an image too", post.domain.display_title("# ![logo](/l.png) Title here\n", "slug"), "logo Title here")
check("folder names read as labels", post.domain.disambiguate_titles([("Intro", "https://s/api-v2/intro"), ("Intro", "https://s/getting_started/intro")]),
      ["Api V2 › Intro", "Getting Started › Intro"])


# ---- absolutize_links -----------------------------------------------------------------------------------------------
PAGE = "https://docs.example.com/open-source/customize/tools/add"
check("a root-relative link becomes absolute",
      post.domain.absolutize_links("See [x](/open-source/quickstart).", PAGE), "See [x](https://docs.example.com/open-source/quickstart).")
check("`./` and `../` resolve against the page", post.domain.absolutize_links("[a](./remove) [b](../agent/basics)", PAGE),
      "[a](https://docs.example.com/open-source/customize/tools/remove) [b](https://docs.example.com/open-source/customize/agent/basics)")
check("`relative=False` leaves `./` alone and still resolves `/`",
      post.domain.absolutize_links("[a](./remove) [b](/x)", PAGE, relative=False), "[a](./remove) [b](https://docs.example.com/x)")
check("anchors, absolute URLs, mailto and protocol-relative links are untouched",
      post.domain.absolutize_links("[a](#h) [b](https://y.dev/z) [c](mailto:a@b.c) [d](//cdn.x/y)", PAGE), "[a](#h) [b](https://y.dev/z) [c](mailto:a@b.c) [d](//cdn.x/y)")
check("a link title survives", post.domain.absolutize_links('[a](/x "The x")', PAGE), '[a](https://docs.example.com/x "The x")')
check("images too", post.domain.absolutize_links("![logo](/img/logo.png)", PAGE), "![logo](https://docs.example.com/img/logo.png)")
fenced = "```md\n[a](/x)\n```\n\nand `[b](/y)` inline, but [c](/z) is a link.\n"
check("code is left as written, prose is not",
      post.domain.absolutize_links(fenced, PAGE), "```md\n[a](/x)\n```\n\nand `[b](/y)` inline, but [c](https://docs.example.com/z) is a link.\n")
check("a stray backtick run in the line does not turn prose into code (CommonMark pairs runs of equal length)",
      post.domain.absolutize_links("`...` `` and ` ```! ` blocks in [skills](/docs/skills)", PAGE),
      "`...` `` and ` ```! ` blocks in [skills](https://docs.example.com/docs/skills)")
check("a double-backtick span guards its content", post.domain.absolutize_links("``a `[x](/y)` b`` then [z](/w)", PAGE),
      "``a `[x](/y)` b`` then [z](https://docs.example.com/w)")
check("a fence of four backticks is not closed by a fence of three inside it",
      post.domain.absolutize_links("````md\n```py\n[a](/x)\n```\n[b](/y)\n````\n\n[c](/z)", PAGE),
      "````md\n```py\n[a](/x)\n```\n[b](/y)\n````\n\n[c](https://docs.example.com/z)")
check("a tilde fence is closed by tildes only", post.domain.absolutize_links("~~~\n```\n[a](/x)\n~~~\n[b](/y)", PAGE),
      "~~~\n```\n[a](/x)\n~~~\n[b](https://docs.example.com/y)")
check("a link inside a callout's fence is code too", post.domain.absolutize_links("> ```md\n> [a](/x)\n> ```\n> [b](/y)", PAGE),
      "> ```md\n> [a](/x)\n> ```\n> [b](https://docs.example.com/y)")
check("raw GitHub files are not rewritten (their `/x` is the repository's)",
      post.domain.absolutize_links("[a](/docs/x.md)", "https://raw.githubusercontent.com/o/r/main/README.md"), "[a](/docs/x.md)")
check("no page URL: untouched", post.domain.absolutize_links("[a](/x)", ""), "[a](/x)")


# ---- a split bundle: short real pages kept, stubs dropped, links resolved per section ---------------------------------
PROSE = "Documentation prose that says something useful about the thing. " * 12
LONG = "A long enough paragraph of documentation. " * 1500


def section(title: str, url: str, body: str) -> str:
    return f"# {title}\nSource: {url}\n\n{body}\n\n"


BUNDLE = (
    section("Advanced", "https://s.dev/docs/advanced", PROSE + "\nSee [intro](./intro) and [guide](/docs/guide).")
    + section("Intro", "https://s.dev/docs/intro", PROSE)
    + section("Quickstart", "https://s.dev/docs/quickstart", PROSE + LONG)
    + section("Remove Tools", "https://s.dev/docs/remove", "Exclude tools.\n\n```python\ntools = Tools(exclude_actions=['wait'])\n```")
    + section("Stub", "https://s.dev/docs/stub", "Just a pointer.")
)
writes, stubs, dupes = post.domain.split_monolith(BUNDLE, "s-dev-llms-full")
titles = [body.splitlines()[0][2:] for _slug, body, _src in writes]
check("a short page with a code example survives the stub filter; a prose stub does not", (titles, stubs), (["Advanced", "Intro", "Quickstart", "Remove Tools"], 1))
advanced = writes[0][1]
check("each page's `./x` links resolve against its own URL, `/x` against its host",
      ("](https://s.dev/docs/intro)" in advanced, "](https://s.dev/docs/guide)" in advanced), (True, True))


RELEASE_TEXT = "What changed in this release, in a sentence that is long enough to count. " * 60
FRAGMENTED = "# Changelog\nSource: https://s.dev/changelog\n\n" + "".join(
    f"**v1.2.{n}**\n\n{RELEASE_TEXT}\n\n## What's Changed\n\n{RELEASE_TEXT}\n\n## New Contributors\n\n{RELEASE_TEXT}\n\n" for n in range(25)
)
PER_VERSION = "# Changelog\nSource: https://s.dev/changelog\n\n" + "".join(f"## 2024.5.{n}\n\n{RELEASE_TEXT}\n\n" for n in range(40))
check("the changelog bodies are over the split cap", (len(FRAGMENTED) > 150_000, len(PER_VERSION) > 150_000), (True, True))
kept_whole = post.domain._h2_subsplit_oversized([("c", FRAGMENTED, "https://s.dev/changelog")], "s")
check("a changelog that the heading split would cut into look-alike pieces stays one page", len(kept_whole), 1)
by_version = post.domain._h2_subsplit_oversized([("c", PER_VERSION, "https://s.dev/changelog")], "s")
check("a changelog with a heading per version is still split, one page each", len(by_version), 40)
not_a_changelog = FRAGMENTED.replace("# Changelog", "# Release guide")
check("the rule is for changelogs only: any other oversized page is split as before",
      len(post.domain._h2_subsplit_oversized([("g", not_a_changelog, "https://s.dev/guide")], "s")) > 1, True)


class FakeMinio:
    def __init__(self):
        self.written: dict[str, str] = {}

    async def write_many(self, batch):
        for key, body, _type in batch:
            self.written[key] = body


class FakeStore:
    """Just enough of `Store` for `post.service.apply_to_store`'s split branch."""

    def __init__(self, bundle: str, nav_index: list[tuple[str, str]]):
        self.framework_slug = "s"
        self.minio = FakeMinio()
        self.nav_index = nav_index
        self._body = bundle
        self.manifest = [ingestion.storage.entities.ManifestEntry(
            idx=0, slug="s-dev-llms-full", url="https://s.dev/llms-full.txt", tier="llms_full", bytes=len(bundle.encode()), key="k0",
        )]

    async def read_body(self, idx):
        return self._body

    async def delete_body_by_key(self, key): pass
    async def delete_vault(self, idx, slug): pass

    async def replace_manifest(self, entries):
        self.manifest = list(entries)


async def split(nav_index: list[tuple[str, str]]) -> tuple[list[str], list[str]]:
    store = FakeStore(BUNDLE, nav_index)
    summary = await post.service.apply_to_store(store)
    check("the split ran", (summary["kind"], summary["was_split"]), ("split", True))
    return [e.title for e in store.manifest], [e.section for e in store.manifest]


async def main() -> None:
    check("with no index the pages keep the bundle's (alphabetical) order, and have no section",
          await split([]), (["Advanced", "Intro", "Quickstart", "Remove Tools"], ["", "", "", ""]))
    check("with the site's index they follow its navigation (an unlisted page goes last)",
          (await split([("https://s.dev/docs/intro.md", ""), ("https://s.dev/docs/quickstart.md", ""), ("https://s.dev/docs/advanced.md", "")]))[0],
          ["Intro", "Quickstart", "Advanced", "Remove Tools"])
    check("…and take its section headings when it has several",
          await split([("https://s.dev/docs/intro.md", "Getting started"), ("https://s.dev/docs/quickstart.md", "Getting started"),
                       ("https://s.dev/docs/advanced.md", "Build"), ("https://s.dev/docs/remove.md", "Build")]),
          (["Intro", "Quickstart", "Advanced", "Remove Tools"], ["Getting started", "Getting started", "Build", "Build"]))
    check("one heading over every page is no grouping: the pages get no section",
          (await split([("https://s.dev/docs/intro.md", "Docs"), ("https://s.dev/docs/quickstart.md", "Docs"),
                        ("https://s.dev/docs/advanced.md", "Docs"), ("https://s.dev/docs/remove.md", "Docs")]))[1],
          ["", "", "", ""])

    # -- Tier 1 fetches the index ----------------------------------------------------------------------------------------
    class FakeProgress:
        async def start(self, tier, total): pass
        async def raise_if_cancelled(self): pass
        async def record_url(self, url, *, status, **rest): pass
        async def update(self, current, last_url=""): pass
        async def finish(self, status="done", error=None): pass

    class TierStore:
        def __init__(self):
            self.nav_index: list[tuple[str, str]] = []
            self.pages: list[dict] = []

        async def add_page(self, **kwargs):
            self.pages.append(kwargs)

    index = "# S\n\n> Docs.\n\n## Getting started\n\n- [Intro](https://s.dev/docs/intro.md): start here\n- [Quickstart](https://s.dev/docs/quickstart.md)\n- [Other site](https://elsewhere.dev/x)\n"

    async def run(handler, **kwargs) -> TierStore:
        real = httpx.AsyncClient

        class Client(real):
            def __init__(self, *args, **kw):
                kw["transport"] = httpx.MockTransport(handler)
                super().__init__(*args, **kw)

        tier1.service.httpx.AsyncClient = Client
        store = TierStore()
        try:
            await tier1.service.run(url="https://s.dev/llms-full.txt", framework_slug="s", progress=FakeProgress(), store=store, **kwargs)
        finally:
            tier1.service.httpx.AsyncClient = real
        return store

    def site(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=index if request.url.path == "/llms.txt" else BUNDLE)

    store = await run(site, nav_url="https://s.dev/llms.txt")
    check("Tier 1 keeps the index's own-host pages with their sections, in its order", store.nav_index,
          [("https://s.dev/docs/intro.md", "Getting started"), ("https://s.dev/docs/quickstart.md", "Getting started")])
    check("a bundle that will be split resolves only `/x` links up front (each section does the rest)", store.pages[0]["relative_links"], False)

    store = await run(site)
    check("no `nav_url`: no order is looked for", store.nav_index, [])

    store = await run(lambda request: httpx.Response(404) if request.url.path == "/llms.txt" else httpx.Response(200, text=BUNDLE), nav_url="https://s.dev/llms.txt")
    check("a missing index never holds the run up: no order, the page is stored", (store.nav_index, len(store.pages)), ([], 1))

    def broken(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/llms.txt":
            raise httpx.ConnectError("down")
        return httpx.Response(200, text=BUNDLE)

    store = await run(broken, nav_url="https://s.dev/llms.txt")
    check("…nor does an unreachable one", (store.nav_index, len(store.pages)), ([], 1))

    small = "# Tiny\nSource: https://s.dev/a\n\n" + "Short bundle text. " * 60
    store = await run(lambda request: httpx.Response(200, text=small if request.url.path != "/llms.txt" else index), nav_url="https://s.dev/llms.txt")
    check("a bundle too small to be split does not fetch the index, and resolves all its links", (store.nav_index, store.pages[0]["relative_links"]), ([], True))

    # -- manifest titles on read -----------------------------------------------------------------------------------------
    manifest = {"entries": [
        {"idx": 0, "slug": "a", "title": "All Parameters", "source_path": f"{BASE}/customize/agent/all-parameters"},
        {"idx": 1, "slug": "b", "title": "All Parameters", "source_path": f"{BASE}/customize/browser/all-parameters"},
        {"idx": 2, "slug": "c", "title": "Quickstart", "source_path": f"{BASE}/quickstart"},
    ]}
    unique = ingestion.storage.service._with_unique_titles(manifest)
    check("a manifest read back gets distinct titles", [e["title"] for e in unique["entries"]], ["Agent › All Parameters", "Browser › All Parameters", "Quickstart"])
    check("…and reading it again changes nothing", ingestion.storage.service._with_unique_titles(unique), unique)
    clean = {"entries": [{"idx": 0, "slug": "a", "title": "A", "source_path": "https://s/x/a"}]}
    check("a manifest with no repeats is returned as it is", ingestion.storage.service._with_unique_titles(clean) is clean, True)
    check("a title falls back to the entry's URL when it has no source path",
          [e["title"] for e in ingestion.storage.service._with_unique_titles({"entries": [
              {"idx": 0, "title": "Intro", "url": "https://s/api/intro"}, {"idx": 1, "title": "Intro", "url": "https://s/sdk/intro"}]})["entries"]],
          ["Api › Intro", "Sdk › Intro"])


asyncio.run(main())


# ---- the normalizer -------------------------------------------------------------------------------------------------
def normalized(text: str) -> str:
    return normalize.domain.normalize_doc(text).body


check("the normalizer is at v5", normalize.versions.NORMALIZER_VERSION, 5)
check("a callout's fence loses its Mintlify attributes",
      normalized('> [!NOTE]\n>\n> ```bash theme={"theme":"css-variables"}\n> pip install "x[video]"\n> ```\n'),
      '> [!NOTE]\n>\n> ```bash\n> pip install "x[video]"\n> ```\n')
check("a plain fence still does", normalized('```python theme={"theme":"css-variables"}\nx = 1\n```\n'), "```python\nx = 1\n```\n")
check("an indented fence keeps its indent", normalized('- item\n\n  ```bash title="a.sh"\n  ls\n  ```\n'), "- item\n\n  ```bash\n  ls\n  ```\n")
check("a self-closing `<video />` goes", normalized("Intro.\n\n<video />\n\n## Features\n"), "Intro.\n\n\n## Features\n")
check("so does a `<video>` of `<source>`s that point nowhere",
      normalized('Before.\n\n<video>\n  <source type="video/mp4" />\n\n  <source type="video/mp4" />\n</video>\n\nAfter.\n'), "Before.\n\n\nAfter.\n")
check("a video with a `src` stays", normalized('<video src="https://x/y.mp4" controls />\n'), '<video src="https://x/y.mp4" controls />\n')
check("…as does one whose `<source>` has it", normalized('<video>\n  <source src="a.mp4" type="video/mp4" />\n</video>\n'), '<video>\n  <source src="a.mp4" type="video/mp4" />\n</video>\n')
check("a `<video />` inside a code fence is code", normalized("```html\n<video />\n```\n"), "```html\n<video />\n```\n")
check("a tag sharing its line with prose stays", normalized("Watch <video /> here.\n"), "Watch <video /> here.\n")
check("an unclosed multi-line `<video>` is left alone", normalized("<video>\ntext\n"), "<video>\ntext\n")
check("a bare `<pre />` no longer hides every fence after it (it opened an HTML block nothing closed)",
      normalized('<pre />\n\n> ```bash theme={null}\n> ls\n> ```\n\n```python theme={null}\nx = 1\n```\n'),
      '<pre />\n\n> ```bash\n> ls\n> ```\n\n```python\nx = 1\n```\n')
once = normalized('> ```bash theme={"a":1}\n> ls\n> ```\n\n<video />\n')
check("the new passes are idempotent", normalized(once), once)
check("they are counted", (normalize.domain.normalize_doc('> ```bash theme={"a":1}\n> ls\n> ```\n\n<video />\n').stats.empty_media_stripped,
                           normalize.domain.normalize_doc('> ```bash theme={"a":1}\n> ls\n> ```\n').stats.fence_meta_stripped), (1, 1))

print("\nall checks passed")
