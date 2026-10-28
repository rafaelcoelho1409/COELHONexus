"""Offline check of Ingestion Tier 1 (`llms-full.txt`) and the dispatcher's source chain.

    cd apps/server && .venv/bin/python ../../scripts/server/test_ingestion_tier1.py

An in-process HTTP transport stands in for the documentation sites and in-memory fakes for Redis / MinIO, so this pins what a
run does with a good bundle, a 404, a soft-404 HTML page, a link index and a flaky server — and that the dispatcher then falls
through to the next declared source instead of failing the run."""
import asyncio
import os
import sys
from pathlib import Path

for name, value in {"REDIS_HOST": "localhost", "REDIS_PORT": "6379", "REDIS_PASSWORD": "x", "ENVIRONMENT": "local"}.items():
    os.environ.setdefault(name, value)
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "server"))

import httpx

import domains

ingestion = domains.dd.ingestion
tier1 = ingestion.tiers.tier1
errors = ingestion.tiers.errors
dispatch = ingestion.runtime.dispatch


def check(label: str, got, want) -> None:
    assert got == want, f"{label}: expected {want!r}, got {got!r}"
    print(f"ok  {label}")


# ---- pure helpers ---------------------------------------------------------------------------------------------------
check("an HTML document is recognised", tier1.domain.looks_like_html("<!DOCTYPE html>\n<html><body>Not found</body></html>"), True)
check("an <html> tag too", tier1.domain.looks_like_html("  \n<html lang='en'>"), True)
check("markdown that quotes HTML later on is not HTML", tier1.domain.looks_like_html("# Title\n\n<html> is a tag"), False)
check("Retry-After in seconds", tier1.domain.retry_after_seconds("7"), 7.0)
check("Retry-After is capped", tier1.domain.retry_after_seconds("86400"), tier1.params.RETRY_AFTER_MAX_S)
check("an HTTP-date Retry-After is ignored", tier1.domain.retry_after_seconds("Wed, 21 Oct 2026 07:28:00 GMT"), None)
check("no Retry-After", tier1.domain.retry_after_seconds(None), None)

entry = {"name": "X", "llms_full": "https://x/llms-full.txt", "sitemap": "https://x/sitemap.xml", "docs": "https://x/docs", "github": "https://github.com/x/x"}
check("the source chain lists every declared source, best first",
      [s["kind"] for s in dispatch.domain.source_chain(entry)], ["llms_full", "sitemap", "docs", "github"])
check("an undeclared tier is skipped",
      [s["kind"] for s in dispatch.domain.source_chain({"docs": "u", "llms_txt": "t"})], ["llms_txt", "docs"])
check("no source at all", dispatch.domain.source_chain({"name": "X"}), [])
check("the chain starts where pick_best does", dispatch.domain.source_chain(entry)[0], dispatch.domain.pick_best(entry))

# what each tier is handed from the catalog (these were `if mod in (…)` branches in the dispatcher)
catalog = {"name": "X", "llms_txt": "https://x/llms.txt", "sitemap": "https://x/sitemap.xml", "path_filter": {"include": ["/docs"]}, "code_language": "python"}
check("Tier 1 gets the path filter and the site's own index as its page order",
      dispatch.domain.tier_arguments("llms_full", catalog), {"path_filter": {"include": ["/docs"]}, "nav_url": "https://x/llms.txt"})
check("Tier 2 gets the code language, the path filter and the sitemap to tell a curated index from the docs",
      dispatch.domain.tier_arguments("llms_txt", catalog), {"code_language": "python", "path_filter": {"include": ["/docs"]}, "sitemap_url": "https://x/sitemap.xml"})
check("Tiers 3 and 4 get the framework's name, the code language and the path filter",
      [dispatch.domain.tier_arguments(kind, catalog) for kind in ("sitemap", "docs")], [{"framework_name": "X", "code_language": "python", "path_filter": {"include": ["/docs"]}}] * 2)
check("Tier 5 gets nothing from the catalog", dispatch.domain.tier_arguments("github", catalog), {})
check("a field the entry lacks is None", dispatch.domain.tier_arguments("docs", {"name": "X"}), {"framework_name": "X", "code_language": None, "path_filter": None})
check("every error that sends the dispatcher to the next source is listed once",
      errors.FALL_THROUGH, (errors.ManifestDetected, errors.EmptyLinksDetected, errors.TierUnavailable))

# the dispatcher's answers
check("a run is found in Langfuse under the framework's label", dispatch.domain.session_id("tempo", "ab12"), "docs-distiller/tempo/ab12")
check("a finished run's metric carries its tier, outcome and size; one with no post summary carries zeros and `unknown`",
      (dispatch.domain.run_metrics({"tier_kind": "docs", "status": "done", "post": {"output_files": 7, "output_bytes": 900}}),
       dispatch.domain.run_metrics({})),
      ({"tier_kind": "docs", "outcome": "done", "output_files": 7, "output_bytes": 900},
       {"tier_kind": "unknown", "outcome": "unknown", "output_files": 0, "output_bytes": 0}))
base = dispatch.domain.base_result("r1", "tempo", {"name": "Tempo"}, {"kind": "docs", "url": "https://t/docs"})
check("every answer after the source is picked carries the run, the framework and the source",
      base, {"run_id": "r1", "slug": "tempo", "framework_name": "Tempo", "tier_kind": "docs", "tier_url": "https://t/docs"})
check("a run that could not start says why, with what is known of it",
      dispatch.domain.rejected_result("r1", "nope", "unknown framework slug: 'nope'"),
      {"run_id": "r1", "slug": "nope", "status": "failed", "error": "unknown framework slug: 'nope'"})
check("…a failed one carries the error's type and message", dispatch.domain.failed_result(base, ValueError("boom"))["error"], "ValueError: boom")
check("…a cancelled one wrote nothing", dispatch.domain.cancelled_result(base)["pages_written"], 0)
check("the gate says who holds the lock and what to do",
      (dispatch.domain.locked_by_other("a", "b", "r9")["slug"], "cancel it before starting 'a'" in dispatch.domain.locked_by_other("a", "b", "r9")["message"],
       dispatch.domain.locked_by_same("a", "r9")["run_id"], dispatch.domain.locked_by_race("a", None)["run_id"]),
      ("b", True, "r9", "?"))
check("the finalized manifest names the framework, source, run and the index's summary (\"\" when there is none)",
      dispatch.domain.finalize_extra({"name": "Tempo"}, base, "r1", None),
      {"framework_name": "Tempo", "tier_kind": "docs", "tier_url": "https://t/docs", "run_id": "r1", "index_summary": ""})


# ---- fakes ----------------------------------------------------------------------------------------------------------
class FakeProgress:
    def __init__(self):
        self.calls: list[tuple] = []
        self.closed = False

    async def start(self, tier, total): self.calls.append(("start", tier, total))
    async def update_total(self, total): self.calls.append(("update_total", total))
    async def raise_if_cancelled(self): pass
    async def record_url(self, url, *, status, **rest): self.calls.append(("record_url", status, rest.get("error_msg")))
    async def update(self, current, last_url=""): self.calls.append(("update", current))
    async def finish(self, status="done", error=None): self.calls.append(("finish", status))
    async def close(self): self.closed = True

    def statuses(self) -> list[str]:
        return [c[1] for c in self.calls if c[0] == "finish"]


class FakeStore:
    def __init__(self):
        self.pages: list[dict] = []
        self.index_summary = ""

    async def add_page(self, **kwargs):
        self.pages.append(kwargs)

    def reorder_by_url_list(self, urls): pass


GOOD = "# Quickstart\nSource: https://x/a\n\n" + "Real documentation text. " * 40 + "\n```py\nx = 1\n```\n"
BIG = "# Big bundle\n\n" + ("A paragraph of documentation text that repeats.\n" * 2000)


def transport(handler):
    real = httpx.AsyncClient

    class Client(real):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    tier1.service.httpx.AsyncClient = Client
    return real


async def run_tier1(handler):
    real = transport(handler)
    progress, store = FakeProgress(), FakeStore()
    try:
        outcome = await tier1.service.run(url="https://docs.example.com/llms-full.txt", framework_slug="demo", progress=progress, store=store)
        return outcome, progress, store, None
    except Exception as error:  # noqa: BLE001
        return None, progress, store, error
    finally:
        tier1.service.httpx.AsyncClient = real


async def main() -> None:
    tier1.params.RETRY_WAIT_INITIAL_S = 0.0  # keep the retry tests fast

    # -- Tier 1 behaviour ----------------------------------------------------------------------------------------------
    outcome, progress, store, error = await run_tier1(lambda request: httpx.Response(200, text=GOOD))
    check("a good bundle is stored as one page", (outcome, error, len(store.pages)), (1, None, 1))
    check("a small bundle is titled by its own heading and keeps its vault", (store.pages[0]["title"], store.pages[0]["defer_vault"]), ("Quickstart", False))
    check("the tier never marks the run done (the dispatcher does)", progress.statuses(), [])

    outcome, progress, store, error = await run_tier1(lambda request: httpx.Response(200, text=BIG))
    check("a bundle that will be split defers its vault (the split pages each get one)", store.pages[0]["defer_vault"], True)

    outcome, progress, store, error = await run_tier1(lambda request: httpx.Response(404, text="nope"))
    check("a 404 is TierUnavailable, handed on as a downgrade — not a failed run", (type(error), progress.statuses(), len(store.pages)), (errors.TierUnavailable, ["downgrade"], 0))
    check("…and says why in the URL log", ("http_error", "HTTP 404") in [(c[1], c[2]) for c in progress.calls if c[0] == "record_url"], True)

    outcome, progress, store, error = await run_tier1(lambda request: httpx.Response(200, text="<!doctype html><html><body>" + "x" * 800 + "</body></html>"))
    check("a soft-404 HTML page is not ingested as markdown", (type(error), len(store.pages)), (errors.TierUnavailable, 0))

    outcome, progress, store, error = await run_tier1(lambda request: httpx.Response(200, text="tiny"))
    check("a body that is too short is TierUnavailable", type(error), errors.TierUnavailable)

    manifest = "# Index\n" + "".join(f"URL: https://x/{n}\n" for n in range(150)) + "padding " * 100
    outcome, progress, store, error = await run_tier1(lambda request: httpx.Response(200, text=manifest))
    check("a link index is ManifestDetected, as before", (type(error), progress.statuses(), len(store.pages)), (errors.ManifestDetected, ["downgrade"], 0))

    attempts = {"n": 0}

    def flaky(request):
        attempts["n"] += 1
        return httpx.Response(503, headers={"retry-after": "0"}) if attempts["n"] < 3 else httpx.Response(200, text=GOOD)

    outcome, progress, store, error = await run_tier1(flaky)
    check("a server that fails twice and then answers is retried to success", (outcome, attempts["n"]), (1, 3))

    attempts["n"] = 0

    def always_down(request):
        attempts["n"] += 1
        return httpx.Response(503)

    outcome, progress, store, error = await run_tier1(always_down)
    check("a server that stays down is asked a bounded number of times, then TierUnavailable", (type(error), attempts["n"]), (errors.TierUnavailable, tier1.params.RETRY_ATTEMPTS))

    attempts["n"] = 0

    def rate_limited(request):
        attempts["n"] += 1
        return httpx.Response(429, headers={"retry-after": "0"}) if attempts["n"] == 1 else httpx.Response(200, text=GOOD)

    outcome, progress, store, error = await run_tier1(rate_limited)
    check("a 429 is retried (and its Retry-After honoured)", (outcome, attempts["n"]), (1, 2))

    attempts["n"] = 0

    def unreachable(request):
        attempts["n"] += 1
        raise httpx.ConnectError("no route", request=request)

    outcome, progress, store, error = await run_tier1(unreachable)
    check("a network error is retried, then TierUnavailable", (type(error), attempts["n"]), (errors.TierUnavailable, tier1.params.RETRY_ATTEMPTS))

    attempts["n"] = 0

    def forbidden(request):
        attempts["n"] += 1
        return httpx.Response(403)

    outcome, progress, store, error = await run_tier1(forbidden)
    check("a 403 is final (not retried)", (type(error), attempts["n"]), (errors.TierUnavailable, 1))

    # -- path_filter: one bundle, more than one product (Browser Use) ----------------------------------------------------
    def bundle(*paths: str) -> str:
        return "".join(
            f"# Page {n}\nSource: https://docs.example.com{path}\n\n{'Text for this page. ' * 30}\n\n" for n, path in enumerate(paths)
        )

    mixed = bundle("/cloud/a", "/open-source/b", "/cloud/c", "/open-source/d", "/changelog")
    keep_open = {"include": ["/open-source/"]}
    trimmed, kept, total = tier1.domain.apply_path_filter(mixed, keep_open)
    check("the filter keeps only the sections inside `include`", (kept, total), (2, 5))
    check("…in their original order, whole", [line for line in trimmed.splitlines() if line.startswith("Source:")],
          ["Source: https://docs.example.com/open-source/b", "Source: https://docs.example.com/open-source/d"])
    check("an entry with no filter is untouched", tier1.domain.apply_path_filter(mixed, None), (mixed, 0, 0))
    check("an empty filter is untouched", tier1.domain.apply_path_filter(mixed, {}), (mixed, 0, 0))
    check("`exclude` alone trims too", tier1.domain.apply_path_filter(mixed, {"exclude": ["/cloud/"]})[1:], (3, 5))
    check("only the declared filter counts: the default noise filter stays off (/changelog survives)",
          tier1.domain.apply_path_filter(mixed, {"exclude": ["/cloud/"]})[0].count("/changelog"), 1)
    no_markers = "# C\n\n## Cache Components\n\n" + "A long page of text without any source line. " * 40
    check("a bundle with no `Source:` markers cannot be told apart by path, so it comes back whole (Next.js)",
          tier1.domain.apply_path_filter(no_markers, {"include": ["/docs/"]}), (no_markers, 0, 0))

    real = tier1.service.httpx.AsyncClient

    async def run_filtered(text, path_filter):
        class Client(real):
            def __init__(self, *args, **kwargs):
                kwargs["transport"] = httpx.MockTransport(lambda request: httpx.Response(200, text=text))
                super().__init__(*args, **kwargs)
        tier1.service.httpx.AsyncClient = Client
        progress, store = FakeProgress(), FakeStore()
        try:
            await tier1.service.run(url="https://docs.example.com/llms-full.txt", framework_slug="demo", progress=progress, store=store, path_filter=path_filter)
            return progress, store, None
        except Exception as error:  # noqa: BLE001
            return progress, store, error
        finally:
            tier1.service.httpx.AsyncClient = real

    progress, store, error = await run_filtered(mixed, keep_open)
    stored = store.pages[0]["body"]
    check("run() stores only the library's sections", (error, "/cloud/" in stored, stored.count("Source:")), (None, False, 2))
    progress, store, error = await run_filtered(bundle("/cloud/a", "/cloud/b", "/cloud/c", "/cloud/d"), keep_open)
    check("a filter that leaves nothing is TierUnavailable, not an empty page", (type(error), len(store.pages), progress.statuses()), (errors.TierUnavailable, 0, ["downgrade"]))
    progress, store, error = await run_filtered(no_markers + "padding " * 100, {"include": ["/docs/"]})
    check("a filter on a bundle without source markers stores it whole", (error, len(store.pages)), (None, 1))

    # -- Tier 2: an index that cannot be fetched falls through ---------------------------------------------------------
    tier2 = ingestion.tiers.tier2
    real2 = tier2.service.httpx.AsyncClient

    async def run_tier2(handler, path_filter=None):
        class Client(real2):
            def __init__(self, *args, **kwargs):
                kwargs["transport"] = httpx.MockTransport(handler)
                super().__init__(*args, **kwargs)
        tier2.service.httpx.AsyncClient = Client
        progress, store = FakeProgress(), FakeStore()
        try:
            await tier2.service.run(url="https://docs.example.com/llms.txt", framework_slug="demo", progress=progress, store=store, path_filter=path_filter)
            return progress, store, None
        except Exception as error:  # noqa: BLE001
            return progress, store, error
        finally:
            tier2.service.httpx.AsyncClient = real2

    progress, store, error = await run_tier2(lambda request: httpx.Response(404, text="nope"))
    check("Tier 2: a 404 index is TierUnavailable (it used to end the run with a RuntimeError)", (type(error), progress.statuses()), (errors.TierUnavailable, ["downgrade"]))

    def unreachable_index(request):
        raise httpx.ConnectError("no route", request=request)

    progress, store, error = await run_tier2(unreachable_index)
    check("Tier 2: an unreachable index too", (type(error), progress.statuses()), (errors.TierUnavailable, ["downgrade"]))

    index = (
        "# Demo\n\n- [Cloud A](https://docs.example.com/cloud/a): cloud\n- [Open B](https://docs.example.com/open-source/b): lib\n"
        "- [Open D](https://docs.example.com/open-source/d): lib\n"
    )

    def serve_site(request):
        if request.url.path == "/llms.txt":
            return httpx.Response(200, text=index)
        return httpx.Response(200, text="# Page\n\n" + "Documentation text. " * 80)  # over Tier 2's 1 KB stub floor

    progress, store, error = await run_tier2(serve_site, keep_open)
    check("Tier 2 with a path_filter fetches only the library's pages", (error, sorted(p["url"].rsplit("/", 1)[1] for p in store.pages)), (None, ["b", "d"]))
    progress, store, error = await run_tier2(serve_site, None)
    check("Tier 2 without one fetches every listed page", (error, len(store.pages)), (None, 3))
    progress, store, error = await run_tier2(serve_site, {"include": ["/nowhere/"]})
    check("Tier 2: a filter that leaves no link is TierUnavailable", (type(error), len(store.pages)), (errors.TierUnavailable, 0))

    # -- the dispatcher's source chain ---------------------------------------------------------------------------------
    chain = dispatch.domain.source_chain(entry)
    made: list[FakeProgress] = []

    def new_progress():
        made.append(FakeProgress())
        return made[-1]

    async def scenario(outcomes: dict[str, object]):
        ran: list[str] = []
        base = {"tier_kind": chain[0]["kind"], "tier_url": chain[0]["url"]}
        first = FakeProgress()
        made.clear()

        async def run_tier(kind, url, tier_progress):
            ran.append(kind)
            result = outcomes.get(kind)
            if isinstance(result, Exception):
                raise result

        try:
            final = await dispatch.service.run_source_chain(chain, first, new_progress=new_progress, run_tier=run_tier, base_result=base, slug="demo")
            return ran, base, final is first, first, None
        except Exception as error:  # noqa: BLE001
            return ran, base, None, first, error

    ran, base, same, first, error = await scenario({})
    check("the best source works: nothing else is tried", (ran, error, same), (["llms_full"], None, True))

    ran, base, same, first, error = await scenario({"llms_full": errors.TierUnavailable("404")})
    check("a broken tier 1 falls through to the next declared source", (ran, error), (["llms_full", "sitemap"], None))
    check("…which gets a fresh progress, and the run reports the tier that actually ran", (same, len(made), base["tier_kind"], first.closed, first.statuses()), (False, 1, "sitemap", True, ["downgrade"]))

    ran, base, same, first, error = await scenario({"llms_full": errors.ManifestDetected("index")})
    check("a link index falls through as well (to the next declared tier, even when it is not llms.txt)", ran, ["llms_full", "sitemap"])

    ran, base, same, first, error = await scenario({"llms_full": errors.TierUnavailable("404"), "sitemap": errors.EmptyLinksDetected("u")})
    check("a fall-through tier can itself fall through", ran, ["llms_full", "sitemap", "docs"])
    check("…and an empty link list leaves a downgrade marker in the URL log", any(c[:2] == ("record_url", "downgrade") for c in made[0].calls), True)

    ran, base, same, first, error = await scenario({k["kind"]: errors.TierUnavailable(k["kind"]) for k in chain})
    check("when every source is unusable the run fails, naming each reason", (type(error), all(k["kind"] in str(error) for k in chain)), (RuntimeError, True))

    ran, base, same, first, error = await scenario({"llms_full": RuntimeError("boom")})
    check("any other error ends the run and tries nothing else", (ran, type(error)), (["llms_full"], RuntimeError))

    chain[:] = chain[:1]
    ran, base, same, first, error = await scenario({"llms_full": errors.TierUnavailable("404")})
    check("with no lower tier declared, an unusable source fails the run", (ran, type(error)), (["llms_full"], RuntimeError))

    # -- storage: the deferred vault and the cleanup of replaced pages ----------------------------------------------------
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

    def vault_keys(minio):
        return sorted(k for k in minio.objects if k.startswith("synth-vault/"))

    minio = FakeMinio()
    store = ingestion.storage.service.Store("run-1", "demo", FakeRedis(), minio)
    page = "# Page\n\n```py\nx = 1\n```\n" + "text. " * 80
    await store.add_page(slug="kept", url="", body=page, tier="llms_full")
    check("a page gets its vault when added", len(vault_keys(minio)), 2)
    await store.add_page(slug="deferred", url="", body=page, tier="llms_full", defer_vault=True)
    check("a deferred page does not (the backfill makes it after the split)", len(vault_keys(minio)), 2)
    check("…but its body and its raw copy are stored", sum(1 for k in minio.objects if "deferred" in k and k.startswith(("ingestion/", "ingestion-raw/"))), 2)
    await store.delete_vault(0, "kept")
    check("deleting a replaced page's vault removes both blobs", vault_keys(minio), [])

    print("\nall checks passed")


asyncio.run(main())
