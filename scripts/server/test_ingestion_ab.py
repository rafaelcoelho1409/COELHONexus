"""Offline check of the capture (`ingestion/raw/{slug}/`) and of the A/B harness built on it.

    cd apps/server && .venv/bin/python ../../scripts/server/test_ingestion_ab.py

Pins three things: a tier's capture is the bytes the site sent and reads back as they were; arm A (`ab.current`, the tier code run on a
capture) makes exactly the pages — bodies, slugs, titles as read, sections, tiers, order — that a real Tier 1 run followed by the post
step stores (the proof that the capture/transform split changed nothing); and the harness's measures and verdict rule say what they
claim. In-process HTTP and in-memory MinIO/Redis stand in for the world; `scratchpad`-style runs on the real bundles use the same
`live_pages` / `arm_pages` helpers."""
import asyncio
import os
import sys
import tempfile
import types
from dataclasses import asdict
from pathlib import Path

for name, value in {"REDIS_HOST": "localhost", "REDIS_PORT": "6379", "REDIS_PASSWORD": "x", "ENVIRONMENT": "local"}.items():
    os.environ.setdefault(name, value)
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "server"))

import httpx

import domains

ingestion = domains.dd.ingestion
tier1 = ingestion.tiers.tier1
capture = ingestion.capture
ab = ingestion.ab
Page = ingestion.transform.entities.Page


def check(label: str, got, want) -> None:
    assert got == want, f"{label}: expected {want!r}, got {got!r}"
    print(f"ok  {label}")


class FakeMinio:
    def __init__(self):
        self.objects: dict[str, str] = {}

    async def write(self, key, text, content_type=""): self.objects[key] = text
    async def delete(self, key): self.objects.pop(key, None)
    async def read_text(self, key): return self.objects[key]
    async def write_many(self, items):
        for key, text, _ctype in items:
            self.objects[key] = text


class FakeRedis:
    async def set(self, *args, **kwargs): pass


class FakeProgress:
    async def start(self, tier, total): pass
    async def raise_if_cancelled(self): pass
    async def record_url(self, url, *, status, **rest): pass
    async def update(self, current, last_url=""): pass
    async def finish(self, status="done", error=None): pass


URL = "https://docs.example.com/llms-full.txt"
NAV_URL = "https://docs.example.com/llms.txt"


def section(i: int, *, noise: bool = True) -> str:
    """A bundle section the way Mintlify-style hosts write them: an H1, its `Source:` line, JSX wrappers, a fenced example."""
    lines = [f"# Page {i:02d}", f"Source: https://docs.example.com/docs/page-{i:02d}", ""]
    if noise:
        lines += ["<Note>", f"Heads up on page {i}.", "</Note>", ""]
    lines += [f"Page {i} explains one feature of the library, with a [link](/docs/other-{i}) and an example. " * 20, "",
              "```python theme={null}", f"def feature_{i}():", f"    return {i}  # [!code highlight]", "```", "", ""]
    return "\n".join(lines)


def bundle_with_sources(n: int = 40) -> str:
    return "".join(section(i) for i in range(n))


def nav_for(n: int = 40) -> str:
    """The site's own index: pages in REVERSE of the bundle's order, filed under two sections."""
    out = ["# Example Docs", "", "> Docs.", ""]
    for part, ids in (("Guides", [i for i in reversed(range(n)) if i % 2]), ("Reference", [i for i in reversed(range(n)) if i % 2 == 0])):
        out += [f"## {part}", ""] + [f"- [Page {i:02d}](https://docs.example.com/docs/page-{i:02d}.md): about {i}" for i in ids] + [""]
    return "\n".join(out)


def docusaurus_bundle() -> str:
    """One H1 over everything (the splitter must fall through to H2), no `Source:` lines."""
    out = ["# Whole Docs", ""]
    for i in range(60):
        out += [f"## Topic {i:02d}", "", f"Topic {i} is described here in some detail. " * 80, "", "```bash", f"run --topic {i}", "```", ""]
    return "\n".join(out) + "\n"


def changelog_bundle() -> str:
    body = ["# Changelog", "Source: https://docs.example.com/docs/changelog", ""]
    for i in range(30):
        body += [f'<Update label="v1.{i}.0">', f"## Release 1.{i}.0", f"Changes in release {i}. " * 250, "</Update>", ""]
    return section(0) + section(1) + section(2) + "\n".join(body) + "\n"


async def live_pages(
    bundle: str,
    *,
    nav_text: str = "",
    path_filter: dict | None = None,
    slug: str = "demo",
    routes: dict[str, str] | None = None,
) -> tuple[list[dict], FakeMinio]:
    """What a real Tier 1 run and the post step leave in the store, page by page, as the explorer reads them (`routes`: other paths of
    the site and what they answer, e.g. the later parts of a paginated bundle)."""
    minio = FakeMinio()
    store = ingestion.storage.service.Store("run-1", slug, FakeRedis(), minio)
    real = httpx.AsyncClient

    def site(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/llms.txt":
            return httpx.Response(200, text=nav_text) if nav_text else httpx.Response(404)
        if routes and request.url.path in routes:
            return httpx.Response(200, text=routes[request.url.path])
        return httpx.Response(200, text=bundle)

    class Client(real):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(site)
            super().__init__(*args, **kwargs)

    httpx.AsyncClient = Client
    try:
        await tier1.service.run(
            url=URL, framework_slug=slug, progress=FakeProgress(), store=store, path_filter=path_filter, nav_url=NAV_URL if nav_text else None,
        )
        await ingestion.post.service.apply_to_store(store)
    finally:
        httpx.AsyncClient = real
    view = ingestion.storage.domain.with_unique_titles({"entries": [asdict(e) for e in store.manifest]})["entries"]
    pages = [
        {"slug": e["slug"], "title": e["title"], "section": e["section"], "source_url": e["source_path"], "tier": e["tier"], "body": minio.objects[e["key"]]}
        for e in view
    ]
    return pages, minio


async def arm_pages(minio: FakeMinio, slug: str = "demo") -> list[dict]:
    """What arm A makes of the capture the run above left in `minio`."""
    inputs = await ab.service.prepare(minio, slug)
    pages, _seconds = await ab.service.run_arm("current", inputs)
    return [{**asdict(p), "tier": p.tier or "llms_full"} for p in pages]


async def main() -> None:
    # ---- the capture -------------------------------------------------------------------------------------------------------
    minio = FakeMinio()
    kept = capture.domain.new_capture(
        "demo", "llms_full", URL,
        [capture.domain.captured_file(name="bundle.md", role="page", url=URL, final_url=URL, status=200, content_type="text/plain", text="héllo\n")],
        capture_params={"path_filter": {"include": ["/docs/"]}},
    )
    await capture.service.save(minio, kept, {"bundle.md": "héllo\n"})
    check("a capture is its files and, last, its description", sorted(minio.objects), ["ingestion/raw/demo/capture.json", "ingestion/raw/demo/files/bundle.md"])
    loaded = await capture.service.load(minio, "demo")
    check("it reads back as it was written", (loaded.files, loaded.params, loaded.tier), (kept.files, {"path_filter": {"include": ["/docs/"]}}, "llms_full"))
    check("…with the hash and size of the text as stored", (loaded.files[0].sha256 == capture.domain.sha256_text("héllo\n"), loaded.files[0].bytes), (True, 7))
    check("a file of the capture reads as text, one it lacks as empty", (await capture.service.read_text(minio, loaded, "bundle.md"), await capture.service.read_text(minio, loaded, "nope")), ("héllo\n", ""))
    check("a framework never captured has none", await capture.service.load(minio, "other"), None)
    minio.objects["ingestion/raw/demo/capture.json"] = '{"version": 99, "files": []}'
    check("a capture from a newer schema is not read", await capture.service.load(minio, "demo"), None)
    check("the raw folder is not a framework", ingestion.storage.domain.framework_slugs(["raw/", "claude-code/", "go"]), ["claude-code", "go"])
    check("raw copies live beside the frameworks, not inside one", (ingestion.storage.keys.raw_prefix("go"), ingestion.storage.keys.framework_prefix("go")), ("ingestion/raw/go/", "ingestion/go/"))
    check("a response that cannot be a bundle says why",
          [tier1.domain.unusable_reason(404, "x" * 900), tier1.domain.unusable_reason(200, "tiny"), tier1.domain.unusable_reason(200, "<html>" + "x" * 900), tier1.domain.unusable_reason(200, "# ok\n" * 200)],
          ["HTTP 404", "body too short (4B)", "HTML page instead of a markdown bundle", None])

    # ---- arm A == the live pipeline ----------------------------------------------------------------------------------------
    scenarios = {
        "a small bundle stays one page": (section(0), "", None),
        "a bundle with `Source:` lines is split, the site's index orders and sections it": (bundle_with_sources(), nav_for(), None),
        "…and so is one cut to the catalog's path_filter": (bundle_with_sources() + "# Elsewhere\nSource: https://docs.example.com/blog/x\n\n" + "Blog text. " * 80, nav_for(), {"include": ["/docs/"]}),
        "a single-H1 bundle falls through to its H2s": (docusaurus_bundle(), "", None),
        "a changelog's releases carry their own tier": (changelog_bundle(), "", None),
        "a bundle with no index keeps its own order": (bundle_with_sources(), "", None),
    }
    for label, (bundle, nav_text, path_filter) in scenarios.items():
        live, store_minio = await live_pages(bundle, nav_text=nav_text, path_filter=path_filter)
        made = await arm_pages(store_minio)
        check(f"arm A makes the live run's pages: {label} ({len(live)} pages)", [{k: p[k] for k in live[0]} for p in made], live)
    banner = "> This is part {n} of 2 of the full documentation (pages 1–40 of 40).\n> The content is paginated: fetch every part to see all of it.\n> {link}\n> Page index: https://docs.example.com/llms.txt\n\n"
    part1 = banner.format(n=1, link="Next part: https://docs.example.com/llms-full.txt/1") + "".join(section(i) for i in range(20))
    part2 = banner.format(n=2, link="Previous part: https://docs.example.com/llms-full.txt") + "".join(section(i) for i in range(20, 40))
    live, paginated_minio = await live_pages(part1, nav_text=nav_for(), routes={"/llms-full.txt/1": part2})
    made = await arm_pages(paginated_minio)
    check(f"arm A makes the live run's pages from a paginated capture too ({len(live)} pages)", [{k: p[k] for k in live[0]} for p in made], live)
    check("…all of both parts, and none of the later part's banner",
          (len(live), "part 2 of 2" in "".join(p["body"] for p in live), sorted(k.rsplit("/", 1)[-1] for k in paginated_minio.objects if "/files/" in k)),
          (40, False, ["bundle.md", "bundle.part2.md", "llms.txt"]))

    live, store_minio = await live_pages(bundle_with_sources(), nav_text=nav_for())
    check("(the index really did reorder and section them)", (live[0]["source_url"], live[0]["section"], len({p["section"] for p in live})), ("https://docs.example.com/docs/page-39", "Guides", 2))
    raw_bundle = store_minio.objects["ingestion/raw/demo/files/bundle.md"]
    check("the capture holds the bundle exactly as the site sent it (JSX, theme attrs, highlighter marks and all)",
          (raw_bundle == bundle_with_sources(), "theme={null}" in raw_bundle, "<Note>" in raw_bundle), (True, True, True))
    check("…and the page copy beside it is not kept twice", [k for k in store_minio.objects if "/raw/demo/pages/" in k], [])
    check("the index is captured too, when the bundle was split", "ingestion/raw/demo/files/llms.txt" in store_minio.objects, True)
    _, small_minio = await live_pages(section(0), nav_text=nav_for())
    check("a bundle that stays one page does not ask for it", "ingestion/raw/demo/files/llms.txt" in small_minio.objects, False)

    # ---- the measures ------------------------------------------------------------------------------------------------------
    ref = ab.domain.analyze("# T\n\nPlain words with a [link](https://x.dev/a).\n\n```py\nx -&gt; y\n```\n\n<Note>\n")
    check("a text is read as its code blocks and the words around them", (ref.fences, sorted(ref.tokens)), (["x -&gt; y"], ["a", "link", "note", "plain", "t", "with", "words"]))
    out = ab.domain.analyze("# T\n\nPlain words with a [link](/a).\n\n```py\nx -> y\n```\n")
    check("an entity decoded inside code is a declared change, not a lost block",
          ab.domain.fence_fidelity(ref, out), {"distinct": 1, "strict_changed": 1, "tolerant_changed": 0})
    check("a block that really changed counts as changed", ab.domain.fence_fidelity(ref, ab.domain.analyze("```py\nx = 1\n```\n"))["tolerant_changed"], 1)
    check("a highlighter annotation on a line of code is a declared change",
          ab.domain.fence_fidelity(ab.domain.analyze("```py\nx = 1  # [!code highlight]\n```\n"), ab.domain.analyze("```py\nx = 1\n```\n")), {"distinct": 1, "strict_changed": 1, "tolerant_changed": 0})
    overlap = ab.domain.token_overlap(ref, out)
    check("making a link absolute invents no word; dropping the component tag loses one", (overlap["precision"], round(overlap["recall"], 3)), (1.0, 0.857))
    invented = ab.domain.analyze("# T\n\nPlain words with a [link](/a) and a sentence nobody wrote.\n")
    check("words the source never had lower precision", ab.domain.token_overlap(ref, invented)["precision"] < 0.99, True)
    check("a relative link is noise, a relative image is not (the server relinks images after every arm)",
          ab.domain.noise_counts(["See [the guide](guide/start) and ![shot](images/a.png), or [the site](https://x.dev/a)."]), {"relative_link": 1})
    check("noise is counted outside code only", ab.domain.noise_counts(ab.domain.analyze("<Note>\n## [](#a) Intro\n```\n<Note>\n[](#b)\n```\n").prose_lines), {"jsx_component_line": 1, "empty_permalink": 1})

    pages = [Page(slug=f"p{i}", title=f"Page {i}", body=f"# Page {i}\n\n" + "words " * 80, source_url=f"https://x.dev/docs/p{i}") for i in range(4)]
    nav_links = [(f"Page {i}", f"https://x.dev/docs/p{i}", "") for i in range(4)] + [("Blog", "https://x.dev/blog/b", "")]
    agreement = ab.domain.nav_agreement(pages, [f"Page {i}" for i in range(4)], nav_links, {"include": ["/docs/"]})
    check("pages line up with the index inside the catalog's scope", (agreement["nav"], agreement["source_coverage"], agreement["source_spurious"], agreement["title_exact"], agreement["agreement"]), (4, 1.0, 0.0, 1.0, 1.0))
    bare = [Page(slug=p.slug, title=p.title, body=p.body) for p in pages] * 2
    check("without `Source:` URLs it falls back to titles, and a bundle cut twice too small scores half",
          ab.domain.nav_agreement(bare, [f"Page {i}" for i in range(4)] * 2, nav_links, {"include": ["/docs/"]})["agreement"], 0.5)

    inputs = ab.entities.Inputs(framework_slug="demo", tier="llms_full", url=URL, text="# T\n\n" + "words " * 80, nav_links=nav_links, params={"path_filter": {"include": ["/docs/"]}})
    card = ab.domain.scorecard(inputs, pages)
    check("a scorecard holds the fidelity, noise, structure and agreement measures",
          sorted(card), sorted(["pages", "bytes", "reference_bytes", "small_pages", "oversized_pages", "wrong_size_share", "no_h1", "duplicate_titles", "duplicate_bodies", "sections",
                                "fences", "code_changed_share", "recall", "precision", "noise", "noise_per_mb", "reference_noise", "nav", "agreement"]))

    # ---- the verdict, by the rule fixed beforehand --------------------------------------------------------------------------
    a = {"precision": 1.0, "recall": 0.95, "code_changed_share": 0.01, "noise_per_mb": 100.0, "wrong_size_share": 0.20, "agreement": 0.6}
    better = {**a, "noise_per_mb": 10.0, "wrong_size_share": 0.05}
    check("B clears the gates and is better on two measures without being worse on any: B", ab.domain.verdict(a, better)["winner"], "B")
    check("one better measure is not enough: a tie, which keeps A", ab.domain.verdict(a, {**a, "noise_per_mb": 10.0})["winner"], "tie")
    check("B that invents text loses whatever else it gains", ab.domain.verdict(a, {**better, "precision": 0.9})["winner"], "A")
    check("B that loses recall loses", ab.domain.verdict(a, {**better, "recall": 0.80})["winner"], "A")
    check("B that damages code loses", ab.domain.verdict(a, {**better, "code_changed_share": 0.05})["winner"], "A")
    check("B worse on two measures loses", ab.domain.verdict(a, {**a, "noise_per_mb": 400.0, "wrong_size_share": 0.5})["winner"], "A")
    check("B better on two and worse on one is a tie", ab.domain.verdict(a, {**better, "agreement": 0.2})["winner"], "tie")
    check("a blind judge that prefers B counts as a measure", ab.domain.verdict(a, {**a, "noise_per_mb": 10.0}, judge_win_rate=0.7)["winner"], "B")

    # ---- running arms, keeping what they made ---------------------------------------------------------------------------------
    check("an arm nobody registered is an error that names the known ones", [type(e).__name__ for e in [_raises(lambda: ab.service.arm_module("agent"))]], ["KeyError"])
    noop = types.ModuleType("noop_arm")

    async def transform(inputs):
        return [Page(slug="only", title="Only", body="# Only\n\nwords\n")]

    noop.transform = transform
    sys.modules["noop_arm"] = noop
    ab.params.ARM_MODULES["noop"] = "noop_arm"
    try:
        pages_b, seconds = await ab.service.run_arm("noop", inputs)
        check("a registered arm is run on the same inputs", ([p.slug for p in pages_b], seconds >= 0), (["only"], True))
        with tempfile.TemporaryDirectory() as tmp:
            card_b = await ab.service.score(inputs, pages_b)
            ab.service.write_result(Path(tmp), "demo", "noop", pages_b, card_b, seconds)
            check("its pages and scorecard are kept to compare later", ([p.body for p in ab.service.read_pages(Path(tmp), "demo", "noop")], ab.service.read_card(Path(tmp), "demo", "noop")["pages"]), (["# Only\n\nwords\n"], 1))
            check("an arm never run has no card", ab.service.read_card(Path(tmp), "demo", "other"), None)
    finally:
        del ab.params.ARM_MODULES["noop"]

    print("\nall checks passed")


def _raises(fn):
    try:
        fn()
    except Exception as error:  # noqa: BLE001
        return error
    raise AssertionError("did not raise")


asyncio.run(main())
