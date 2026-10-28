"""Offline check of the pure helpers that Ingestion's tiers, filters, artifacts and storage keep in their `domain.py` — the logic
that lived inline in the tiers' services (which pages are crawled, how discovery sources merge, what a sitemap says, which slug a
colliding page takes) and now has a home of its own to be tested in.

    cd apps/server && .venv/bin/python ../../scripts/server/test_ingestion_pure.py
"""
import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace

for name, value in {"REDIS_HOST": "localhost", "REDIS_PORT": "6379", "REDIS_PASSWORD": "x", "ENVIRONMENT": "local"}.items():
    os.environ.setdefault(name, value)
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "server"))

import httpx

import domains

ingestion = domains.dd.ingestion
tier2 = ingestion.tiers.tier2.domain
tier3 = ingestion.tiers.tier3.domain
tier4 = ingestion.tiers.tier4.domain
sphinx = ingestion.tiers.tier4.sphinx
tier5 = ingestion.tiers.tier5.domain
filters = ingestion.filters.domain
artifacts = ingestion.artifacts.domain
storage = ingestion.storage.domain


def check(label: str, got, want) -> None:
    assert got == want, f"{label}: expected {want!r}, got {got!r}"
    print(f"ok  {label}")


def response(url: str, content_type: str = "text/html") -> httpx.Response:
    return httpx.Response(200, headers = {"content-type": content_type}, text = "x", request = httpx.Request("GET", url))


# ---- Tier 2: what a fetched page is, and which slug a colliding page takes -------------------------------------------------
check("a page is markdown by its content type or its `.md`, a `.txt` is kept, a `.py` is code, the rest is HTML",
      [tier2.body_kind(response(u, t)) for u, t in [
          ("https://x/a", "text/markdown; charset=utf-8"), ("https://x/a.md", "text/plain"), ("https://x/apilist.txt", "text/plain"),
          ("https://x/ex.py", "text/x-python"), ("https://x/a", "text/html")]],
      ["markdown", "markdown", "text", "python", "html"])
check("a script is fenced as Python", tier2.fence_python("  print(1)\n"), "```python\nprint(1)\n```\n")
LINKS = [("A", "https://x/docs/a", "", ""), ("B", "https://x/blog/b", "", "")]
check("an entry with no path_filter is not filtered", tier2.filter_links(LINKS, None), None)
check("only the catalog's own include / exclude counts, and the number listed comes back",
      tier2.filter_links(LINKS, {"include": ["^/docs/"]}), ([LINKS[0]], 2))
check("a page's slug is its title's, else its URL path's", (tier2.page_slug("Quick Start!", "https://x/a"), tier2.page_slug("", "https://x/docs/a_b")), ("quick-start", "docs-a-b"))
check("the stem that tells a colliding page apart is its nearest folder that is not the slug itself (a trailing `.md` left out)",
      [tier2.slug_stem("https://x/docs/private-cloud/configuration/", "configuration"), tier2.slug_stem("https://x/a/b/index.md", "index"),
       tier2.slug_stem("https://qdrant.tech/", "intro")], ["private-cloud", "b", "qdrant"])
owners = {"intro": "https://x/a/intro"}
check("the first page to want a slug keeps it; the same page asking again keeps it; another takes its stem, then a counter",
      [tier2.free_slug(owners, "intro", "https://x/a/intro"), tier2.free_slug(owners, "intro", "https://x/b/intro"),
       tier2.free_slug({**owners, "intro-b": "https://x/b/intro"}, "intro", "https://x/c/b/intro")],
      ["intro", "intro-b", "intro-b-2"])

# ---- Tier 3: sitemap, URL list, twin probe, slug ---------------------------------------------------------------------------
INDEX = '<?xml version="1.0"?><sitemapindex><sitemap><loc> https://x/s1.xml </loc></sitemap></sitemapindex>'
URLSET = ('<?xml version="1.0"?><urlset><url><loc>https://x/a</loc><lastmod>2026-01-02T00:00:00+00:00</lastmod></url>'
          '<url><loc>https://x/b</loc></url></urlset>')
check("a sitemap index names nested sitemaps", tier3.parse_sitemap(INDEX), (["https://x/s1.xml"], []))
check("a urlset names pages, each with its lastmod or \"\"", tier3.parse_sitemap(URLSET), ([], [("https://x/a", "2026-01-02T00:00:00+00:00"), ("https://x/b", "")]))
check("each URL once, where it first appears", tier3.unique_in_order(["b", "a", "b", "c", "a"]), ["b", "a", "c"])
check("the twin probe asks TWIN_PROBE_SAMPLE pages spread over the list", tier3.twin_sample([f"u{i}" for i in range(10)]), ["u0", "u2", "u5", "u7"])
check("…and none when the list is too short to prove anything", tier3.twin_sample(["u0", "u1"]), [])
check("a twin is a 200 that is long enough and is not HTML",
      [tier3.usable_twin(200, "# T\n" + "word " * 100), tier3.usable_twin(404, "# T\n" + "word " * 100), tier3.usable_twin(200, "<!doctype html>" + "x" * 500), tier3.usable_twin(200, "short")],
      [True, False, False, False])
check("a sitemap URL is fetched when it is in scope and in the language asked for",
      [tier3.wanted("https://x/docs/a", None, polyglot = False, language = None, allow = [], deny = []),
       tier3.wanted("https://x/docs/a", None, polyglot = False, language = "python", allow = ["*/python/*"], deny = []),
       tier3.wanted("https://x/docs/python/a", None, polyglot = False, language = "python", allow = ["*/python/*"], deny = [])],
      [True, False, True])
check("a colliding page takes the nearest distinctive folder, a counter after it",
      [tier3.free_slug({"streaming": "https://x/langchain/streaming"}, "streaming", "https://x/langgraph/streaming"),
       tier3.free_slug({"streaming": "https://x/a/streaming", "streaming-langgraph": "https://x/langgraph/streaming"}, "streaming", "https://x/other/langgraph/streaming")],
      ["streaming-langgraph", "streaming-langgraph-2"])
check("a page's slug is its title's, else its URL path's", tier3.page_slug("", "https://x/docs/a"), "docs-a")

# ---- Tier 4: where the docs are, what discovery found, which of it is crawled ----------------------------------------------
check("the docs' folder is everything before the last `/` of the catalog's URL", [tier4.docs_subtree("/docs/guide/index.html"), tier4.docs_subtree("/"), tier4.docs_subtree("/page"), tier4.docs_subtree("")], ["/docs/guide", "", "", ""])
check("the docs root keeps its trailing slash", [tier4.docs_root("https", "x.dev", "/docs"), tier4.docs_root("https", "x.dev", "")], ["https://x.dev/docs/", "https://x.dev/"])
check("a bare host is probed for its docs; a URL with a path is not",
      (len(tier4.probe_candidates("https://x.dev")), tier4.probe_candidates("https://x.dev/docs/")), (len(ingestion.tiers.tier4.params.DOCS_PROBES), []))
seeds, anchors = tier4.merge_seeds([["https://x/a#s1", "https://x/b", ""], ["https://x/a#s2", "https://x/a", "https://x/c#"]])
check("discovery sources merge in order, a `page#anchor` collapses onto its page, and the anchors are kept",
      (seeds, anchors), (["https://x/a", "https://x/b", "https://x/c"], {"https://x/a": ["s1", "s2"]}))
check("a piece's page is its URL without the fragment, its anchors the page's plus its own",
      tier4.page_identity("https://x/a#s3", anchors), ("https://x/a", ["s1", "s2", "s3"]))
check("only a link on the docs' host and inside its folder is followed",
      [tier4.crawlable_link("https://x/docs/a", "x", "/docs"), tier4.crawlable_link("https://y/docs/a", "x", "/docs"), tier4.crawlable_link("https://x/blog/a", "x", "/docs"), tier4.crawlable_link("https://x/blog/a", "x", "")],
      [True, False, False, True])
check("the httpx pass goes to the browser when more than half its pages failed",
      [tier4.should_escalate(6, 10), tier4.should_escalate(5, 10), tier4.should_escalate(0, 10)], [True, False, False])
check("a sample with no body counts as a single-page app; so does a majority of shells",
      [tier4.is_spa_majority([]), tier4.is_spa_majority(["<div id=root></div>", "x" * 3000 + "<div id=root></div>"]), tier4.is_spa_majority(["<p>" + "word " * 600 + "</p>"])],
      [True, True, False])
check("the sample is the first few pages that are not the root",
      tier4.spa_sample(["https://x/", "https://x/a", "https://x/b", "https://x/c", "https://x/d"]), ["https://x/a", "https://x/b", "https://x/c"])
check("a slow page is worth another try, a missing one is not", [tier4.is_transient_nav_error("net::ERR_TIMED_OUT"), tier4.is_transient_nav_error("HTTP 404")], [True, False])
check("the crawl is compared with the inventory", tier4.oracle_gap({"https://x/a", "https://x/b"}, ["https://x/a#s", "https://x/c"]), (2, {"https://x/b"}, {"https://x/c"}))
check("a piece of a page is named `{page}--{piece}`", tier4.sub_slug("api", "Task.retry"), "api--Task.retry")
check("crawl4ai's own markdown is the fallback: fit, then raw, then the cleaned HTML",
      [tier4.fallback_markdown(SimpleNamespace(markdown = SimpleNamespace(fit_markdown = "fit", raw_markdown = "raw"), cleaned_html = "html")),
       tier4.fallback_markdown(SimpleNamespace(markdown = SimpleNamespace(fit_markdown = "", raw_markdown = "raw"), cleaned_html = "html")),
       tier4.fallback_markdown(SimpleNamespace(markdown = None, cleaned_html = "html")), tier4.fallback_markdown(SimpleNamespace())],
      ["fit", "raw", "html", ""])
check("a discovered URL is crawled when it is on the host, in the language, and inside the path_filter",
      [tier4.wanted("https://x/docs/a", "x", {"include": ["^/docs/"]}, polyglot = False, language = None, allow = [], deny = []),
       tier4.wanted("https://x/blog/a", "x", {"include": ["^/docs/"]}, polyglot = False, language = None, allow = [], deny = []),
       tier4.wanted("https://y/docs/a", "x", None, polyglot = False, language = None, allow = [], deny = [])],
      [True, False, False])
inventory = sphinx.entities.Inventory("p", "1", "https://x/", [
    sphinx.entities.InventoryEntity("a", "std:doc", "https://x/a.html", "", "a"), sphinx.entities.InventoryEntity("a.f", "py:function", "https://x/a.html", "a.f", "a.f"),
    sphinx.entities.InventoryEntity("b", "std:label", "https://x/b.html", "s", "b"), sphinx.entities.InventoryEntity("a2", "std:doc", "https://x/a.html", "", "a")])
check("the inventory's document pages come once each, in its order", sphinx.domain.ordered_doc_pages(inventory), ["https://x/a.html", "https://x/b.html"])

# ---- Tier 5, filters, artifacts, storage -----------------------------------------------------------------------------------
check("a repo URL is read for its org and repo", [tier5.parse_repo("https://github.com/org/repo.git/tree/main"), tier5.parse_repo("https://example.com/org/repo")], [("org", "repo"), None])
check("the API and raw URLs", (tier5.raw_url("o", "r", "main", "docs/a.md"), tier5.tree_api_url("o", "r", "main"), tier5.tree_api_url("o", "r", "main", recursive = False)),
      ("https://raw.githubusercontent.com/o/r/main/docs/a.md", "https://api.github.com/repos/o/r/git/trees/main?recursive=1", "https://api.github.com/repos/o/r/git/trees/main"))
check("a tree listing's files, in order, with their sizes", tier5.blobs_of_tree({"tree": [{"type": "tree", "path": "d"}, {"type": "blob", "path": "d/a.md", "size": 5}, {"type": "blob", "path": ""}, {"type": "blob", "path": "b.md"}]}), [("d/a.md", 5), ("b.md", None)])
check("the catalog's own filter, with the default noise filter off, or None", (filters.declared_filter({"include": ["^/a"]}), filters.declared_filter({}), filters.declared_filter(None)), ({"include": ["^/a"], "disable_defaults": True}, None, None))
check("a URL passes the language filter by its globs, or when there is nothing to judge by",
      [filters.passes_language("https://x/go/a", polyglot = False, language = None, allow = [], deny = []), filters.passes_language("https://x/go/a", polyglot = True, language = "python", allow = ["*/python/*"], deny = [])],
      [True, False])
check("an artifact's name has no slashes around it nor `..`; a folder in it is no name", [artifacts.safe_name(" /a..b.png/ "), artifacts.is_valid_name("a.png"), artifacts.is_valid_name("d/a.png"), artifacts.is_valid_name("")], ["ab.png", True, False, False])
check("an artifact is served with the media type of its extension", [artifacts.media_type_of("a.PNG"), artifacts.media_type_of("a.webm"), artifacts.media_type_of("a.xyz"), artifacts.media_type_of("a")], ["image/png", "video/webm", "application/octet-stream", "application/octet-stream"])
rows = [storage.library_row("a", {"ingested_at": 1.0, "page_count": 3}, {"name": "Alpha", "logo": "l.png"}), storage.library_row("b", {"ingested_at": 5.0, "framework_name": "Beta"}, {})]
check("a library row takes the catalog's name and logo where the manifest has none; the newest ingestion is first",
      ([r["framework_name"] for r in storage.newest_ingested_first(rows)], rows[0]["logo"], rows[0]["page_count"], rows[1]["page_count"]), (["Beta", "Alpha"], "l.png", 3, 0))
check("a host is lower-cased", ingestion.tiers.fetch.domain.host_of("https://Docs.X.dev/a"), "docs.x.dev")

# ---- artifacts: what a fetched media file is, and how references are found and rewritten ------------------------------------
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 100
image = artifacts.artifact_from_response("https://x/a", 200, "image/png; charset=binary", PNG)
check("a 200 with an image body is an artifact, named by its bytes' hash and its type's extension",
      (image.name.endswith(".png"), image.content_type, image.source_url), (True, "image/png", "https://x/a"))
check("…not a 404, a body too small, or an HTML / JSON page served as 200",
      [artifacts.artifact_from_response("https://x/a.png", s, t, b) for s, t, b in [(404, "image/png", PNG), (200, "image/png", b"x"), (200, "text/html", PNG), (200, "application/json", PNG)]], [None] * 4)
check("a body with no content type takes its type from the URL's extension", artifacts.artifact_from_response("https://x/a.webp", 200, None, PNG).content_type, "image/webp")
check("the versioned base is the folder a host's root led to", [artifacts.versioned_base("https://x.dev/en/stable/index.html"), artifacts.versioned_base("https://x.dev/")], ["https://x.dev/en/stable/", "https://x.dev/"])
check("a 404'd image is looked for in `_images/` under the versioned base, then the root; not if it is already there, or not an image",
      [artifacts.image_fallback_urls("https://x.dev/guide/a.png", "https://x.dev/en/stable/"), artifacts.image_fallback_urls("https://x.dev/_images/a.png", None), artifacts.image_fallback_urls("https://x.dev/a.html", None)],
      [["https://x.dev/en/stable/_images/a.png", "https://x.dev/_images/a.png"], [], []])
check("a srcset is reduced to its largest candidate (density or width)", [artifacts.pick_largest_srcset("a.png 1x, b.png 2x", "https://x/p/"), artifacts.pick_largest_srcset("a.png 400w, b.png 800w", "https://x/p/")], ["https://x/p/b.png", "https://x/p/b.png"])
MD = 'Text ![fig](img/a.png "t") and <img src="/b.png" srcset="c.png 1x, d.png 2x"> and ![own](/api/v1/dd/ingestion/s/artifacts/z.png)'
payloads = artifacts.collect_md_payloads(MD, "https://x/docs/page")
check("the references of a Markdown page, resolved", payloads, {"https://x/docs/img/a.png": "remote", "https://x/b.png": "remote", "https://x/docs/d.png": "remote", "https://x/api/v1/dd/ingestion/s/artifacts/z.png": "remote"})
check("this app's own stored copies are never fetched again", sorted(artifacts.not_own_artifacts(payloads)), ["https://x/b.png", "https://x/docs/d.png", "https://x/docs/img/a.png"])
rep = artifacts.build_md_replacement_map(MD, "https://x/docs/page", {"https://x/docs/img/a.png": "/pub/a.png", "https://x/docs/d.png": "/pub/d.png"})
check("the replacement map is keyed on the text as written", rep, {"img/a.png": "/pub/a.png", "c.png 1x, d.png 2x": "/pub/d.png"})
check("replacements are applied longest first, and counted", artifacts.apply_replacements("foo.com/img and foo.com/img.png", {"foo.com/img": "A", "foo.com/img.png": "B"}), ("A and B", 2))
HTML = '<img src="/a.png"><picture><source srcset="b.png 1x, c.png 2x"></picture><a href="mailto:x"><img data-src="data:image/png;base64,AAAA"></a>'
soup = artifacts.parse_html(HTML)
refs = artifacts.html_references(soup, "https://x/docs/")
check("an HTML page's references: `src`, the largest `srcset` candidate, a lazy `data-src`", [(r.attr, r.kind, r.payload) for r in refs],
      [("src", "remote", "https://x/a.png"), ("data-src", "data", "data:image/png;base64,AAAA"), ("srcset", "remote", "https://x/docs/c.png")])
check("…rewritten in place for those whose artifact was stored (a `srcset` also gets a sibling `src`)",
      (artifacts.rewrite_html_references(refs, {"https://x/a.png": "/pub/a.png", "https://x/docs/c.png": "/pub/c.png"}), 'src="/pub/a.png"' in str(soup), 'srcset="/pub/c.png"' in str(soup), '<source src="/pub/c.png"' in str(soup).replace('srcset="/pub/c.png" ', "")),
      (2, True, True, True))

# ---- progress: the shape of a run's records --------------------------------------------------------------------------------
progress = ingestion.runtime.progress.domain
fresh = progress.initial_state(5.0)
check("a run that has not started is idle on the ingest phase", (fresh["status"], fresh["phase"], fresh["current"], fresh["updated_at"]), ("idle", "ingest", 0, 5.0))
check("a run is in flight while it is running or idle", [progress.is_active({"status": s}) for s in ("running", "idle", "done", "failed")] + [progress.is_active(None)], [True, True, False, False, False])
long_url = "https://x/" + "a" * 600
record = progress.url_record(long_url, status = "success", tier = "docs", http_code = 200, fetch_ms = 5, bytes_fetched = 9, extracted_chars = 8, error_msg = "e" * 900, now = 1.0)
check("a URL record keeps the first 500 characters of the URL and of the error", (len(record["url"]), len(record["error_msg"]), record["bytes"]), (500, 500, 9))
check("…and none when there is no error", progress.url_record("u", status = "success", tier = None, http_code = None, fetch_ms = None, bytes_fetched = None, extracted_chars = None, error_msg = None, now = 1.0)["error_msg"], None)
post_summary = progress.post_summary(tier = "docs", input_files = 4, input_bytes = 100, output_files = 6, output_bytes = 90, was_split = True, stubs_dropped = 1, duplicates_dropped = 2, notes = "n" * 300, now = 1.0)
check("the post-processing summary has the expansion ratio and a note cut to 200 characters", (post_summary["expansion_ratio"], len(post_summary["notes"]), post_summary["was_split"]), (1.5, 200, True))
check("…and a ratio of 0 for no input", progress.post_summary(tier = None, input_files = 0, input_bytes = 0, output_files = 0, output_bytes = 0, was_split = False, stubs_dropped = 0, duplicates_dropped = 0, notes = None, now = 1.0)["expansion_ratio"], 0.0)
check("the catalog's regexes: an invalid one is skipped, a match in exclude rejects, include must match",
      [filters.passes_path_filter("https://x/docs/a", {"exclude": ["("], "include": ["^/docs/"]}), filters.passes_path_filter("https://x/docs/a", {"exclude": ["/a$"]}), filters.passes_path_filter("https://x/blog/a", {"include": ["^/docs/"]})],
      [True, False, False])

# ---- a server that hangs up without answering is retried by Tier 4 and Tier 5 too (the shared fetch is checked in the Tier 2 test) ----
async def retried_after_hangup(call) -> tuple[int, int]:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.RemoteProtocolError("Server disconnected without sending a response.", request = request)
        return httpx.Response(200, json = {"ok": True})

    async with httpx.AsyncClient(transport = httpx.MockTransport(handler)) as client:
        response = await call(client)
    return response.status_code if hasattr(response, "status_code") else 200, calls["n"]


check("Tier 4 asks again after a hang-up", asyncio.run(retried_after_hangup(lambda c: ingestion.tiers.tier4.service._get(c, "https://x/a"))), (200, 2))
check("…and so does Tier 5, for a file and for the API", [asyncio.run(retried_after_hangup(lambda c: ingestion.tiers.tier5.service._fetch_blob(c, "o", "r", "main", "a.md"))),
                                                          asyncio.run(retried_after_hangup(lambda c: ingestion.tiers.tier5.service._get_json(c, "https://api.github.com/repos/o/r")))], [(200, 2), (200, 2)])

print("\nall checks passed")
