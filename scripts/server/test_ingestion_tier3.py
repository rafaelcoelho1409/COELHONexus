"""Offline check of the HTML → Markdown extraction the sitemap tier (and Tier 2's / Tier 4's page scrape) shares.

    cd apps/server && .venv/bin/python ../../scripts/server/test_ingestion_tier3.py

Small pages shaped like the ones the Tier 3 assessment of 2026-10-27 found it getting wrong: the `<h1>` kept in a `<header>`
(Docusaurus, Mintlify), Starlight callouts written as `<aside>`, Grafana's breadcrumb and feedback footer, nginx's `<h2>` page
names and its table cells. The real pages were checked live; these keep each case from coming back."""
import os
import sys
from pathlib import Path

for name, value in {"REDIS_HOST": "localhost", "REDIS_PORT": "6379", "REDIS_PASSWORD": "x", "ENVIRONMENT": "local"}.items():
    os.environ.setdefault(name, value)
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "server"))

import domains  # noqa: E402

extract = domains.dd.ingestion.tiers.extract.domain
post = domains.dd.ingestion.post.domain


def check(label: str, got, want) -> None:
    assert got == want, f"{label}: expected {want!r}, got {got!r}"
    print(f"ok  {label}")


def page(body: str, title: str = "Page") -> str:
    return f"<html><head><title>{title}</title></head><body>{body}</body></html>"


def md(body: str, title: str = "Page") -> str:
    return extract.html_to_markdown(page(body, title))


LOREM = "<p>" + "Some documentation text. " * 8 + "</p>"

# ---- the page's <h1> when it sits in a <header> (Docusaurus, Mintlify) -----------------------------------------------------
docusaurus = md(f'<div class="theme-doc-toc-mobile"><button class="tocCollapsibleButton">On this page</button></div>'
                f'<article><div class="theme-doc-markdown markdown"><header><h1>APIRequest</h1></header>{LOREM}</div></article>', "APIRequest | Playwright Python")
check("a page's <h1> kept in a <header> is still its H1 (Playwright)", docusaurus.splitlines()[0], "# APIRequest")
check("…and Docusaurus' collapsed mobile 'On this page' is gone", "On this page" in docusaurus, False)
mintlify = md(f'<main><div id="content-area"><header id="header"><h1>Interrupts</h1></header><div id="content">{LOREM}'
              '<div class="feedback-toolbar"><p>Was this page helpful?</p><button>Yes</button><button>No</button></div>'
              '<div class="callout source-links"><a href="/x">Connect these docs</a></div></div></div></main>', "Interrupts - Docs by LangChain")
check("the same for a Mintlify page", mintlify.splitlines()[0], "# Interrupts")
check("…whose feedback bar and 'Connect these docs' box are page furniture", ("helpful" in mintlify, "Connect these docs" in mintlify), (False, False))
check("a page whose <h1> is in its content is not given a second one", md(f"<main><h1>Title</h1>{LOREM}</main>").count("# Title"), 1)

# ---- callouts written as <aside> (Starlight) -----------------------------------------------------------------------------------
starlight = md(
    '<main><h1>Quick start</h1>'
    '<aside aria-label="Note" class="starlight-aside starlight-aside--note"><p class="starlight-aside__title" aria-hidden="true"><svg></svg>Note</p>'
    '<div class="starlight-aside__content"><p>These two files are now exactly the same.</p>'
    '<pre><code>terragrunt run -- plan</code></pre></div></aside>'
    f'{LOREM}<aside class="right-sidebar"><nav>On this page</nav><p>Sidebar text that is not the page</p></aside></main>')
check("a Starlight callout is kept, as a block quote with its title", ("> **Note**" in starlight, "These two files are now exactly the same." in starlight), (True, True))
check("…and the code block inside it", "terragrunt run -- plan" in starlight, True)
check("…while a sidebar aside is still dropped", "Sidebar text" in starlight, False)
heading = md('<main><h1>Guide</h1><div class="sl-heading-wrapper"><h2 id="overview">Overview</h2><a class="sl-anchor-link" href="#overview">'
             '<span class="sr-only">Section titled “Overview”</span></a></div>' + LOREM + "</main>")
check("text only a screen reader reads (Starlight's 'Section titled …') is gone", "Section titled" in heading, False)

# ---- Grafana's breadcrumb and feedback footer; mkdocs-material's back-to-top ----------------------------------------------------
grafana = md('<main><div class="docs__breadcrumb"><a href="/docs/">Documentation</a><img alt="breadcrumb arrow" src="/a.svg"></div><h1>Quick start</h1>'
             + LOREM + '<div class="docs__feedback print-hidden"><h2>Was this page helpful?</h2><div class="docs__related">Additional helpful documentation</div></div></main>')
check("Grafana's breadcrumb is gone", ("Documentation" in grafana, "breadcrumb" in grafana), (False, False))
check("…and its feedback and related-articles footer", ("helpful" in grafana, "Additional" in grafana), (False, False))
check("…the page is what is left", grafana.splitlines()[0], "# Quick start")
check("a 'Back to top' button is gone", "Back to top" in md(f'<main><h1>T</h1>{LOREM}<button class="md-top md-icon">Back to top</button></main>'), False)

# ---- nginx: names its pages with an <h2>; table cells ----------------------------------------------------------------------------
nginx = md(f'<div id="content"><center><h2>Beginner’s Guide</h2></center>{LOREM}</div>', "nginx: Beginner's Guide")
check("a page with no H1 whose first heading is its name has that heading promoted (nginx)", nginx.splitlines()[0], "# Beginner’s Guide")
other = md(f"<div id='content'><h2>Directives</h2>{LOREM}</div>", "Module ngx_http_gzip_module | nginx")
check("…and one whose first heading is not its name gets the name on top", other.splitlines()[:3], ["# Module ngx_http_gzip_module", "", "## Directives"])
cells = md('<main><h1>gzip</h1><table><tr><th>Syntax:</th><td><code>gzip on | off;</code></td></tr><tr><th>Default:</th><td><pre>gzip off;</pre></td></tr>'
           '<tr><th>Context:</th><td><code>http</code></td></tr></table>' + LOREM + "</main>")
check("a `|` in a table cell is escaped, so the cell stays one cell", "`gzip on \\| off;`" in cells, True)
check("…and a one-line <pre> in a cell is inline code, not a fence", ("```" in cells, "`gzip off;`" in cells), (False, True))
check("a multi-line <pre> in a cell stays what it is", "```" in md("<main><h1>t</h1><table><tr><td><pre>a\nb</pre></td></tr></table>" + LOREM + "</main>"), True)

# ---- ensure_h1 / page_name, on their own ----------------------------------------------------------------------------------------
check("a page name is what is before the site's name", [extract.page_name(t) for t in ("Quick start | Grafana Tempo documentation", "Context - Docs by LangChain", "Plain title", "A - B | Site")],
      ["Quick start", "Context", "Plain title", "A - B"])
check("a page with an H1 is left alone", extract.ensure_h1("# Real\n\ntext\n", "Other | Site"), "# Real\n\ntext\n")
check("a `# comment` in code is no H1", extract.ensure_h1("```bash\n# comment\n```\n", "Setup | Site").startswith("# Setup"), True)
check("a page with no title to go by is left alone", extract.ensure_h1("text\n", ""), "text\n")

# ---- titles: the site's name, and escaped underscores --------------------------------------------------------------------------------
titles = [f"Page {i} | Grafana Tempo documentation" for i in range(10)] + ["Tempo | Grafana Tempo documentation", "Plain",
          "Enterprise A | Grafana Enterprise Traces documentation", *[f"Ent {i} | Grafana Enterprise Traces documentation" for i in range(8)]]
cleaned = post.strip_site_suffix(titles)
check("the site's name every title ends with is taken off", cleaned[:3], ["Page 0", "Page 1", "Page 2"])
check("…and a second suffix with the same last word, seen as often as the minimum", (cleaned[12], cleaned[13]), ("Enterprise A", "Ent 0"))
check("…a title with none stays", cleaned[11], "Plain")
check("…and the result is stable", post.strip_site_suffix(cleaned), cleaned)
rest = [f"{verb} - Users" for verb in ("List", "Get", "Create")] + [f"Topic {i}" for i in range(20)]
check("a suffix on a few titles of a big set (a REST docs set's 'List - Users') is not a site name", post.strip_site_suffix(rest), rest)
check("a small set is left alone", post.strip_site_suffix(["A | S", "B | S", "C | S"]), ["A | S", "B | S", "C | S"])
ts = [f"TypeScript: Documentation - Page {i}" for i in range(10)] + ["TypeScript: The starting point for learning TypeScript", "Plain", "Other thing"]
check("a site's name at the FRONT of a title goes too (typescriptlang.org)", post.strip_site_suffix(ts)[:3], ["Page 0", "Page 1", "Page 2"])
check("…the titles that do not have it stay", post.strip_site_suffix(ts)[10:], ["TypeScript: The starting point for learning TypeScript", "Plain", "Other thing"])
agent = [f"Agent - Overview {i}" for i in range(5)] + [f"Agent - Setup {i}" for i in range(5)]
check("…a first segment shared by every title of a set goes when what is left stays distinct", post.strip_site_suffix(agent)[:2], ["Overview 0", "Overview 1"])
collide = ["Guide - Intro", "Intro"] + [f"Guide - Part {i}" for i in range(9)]
check("…and not when it would make two different titles the same", post.strip_site_suffix(collide), collide)
mixed = [f"Parent › Child {i} | Site" for i in range(12)]
check("`Parent › Child` titles are not touched, nor counted", post.strip_site_suffix(mixed), mixed)
check("an escaped underscore is not part of a title", post.display_title("# Page\n\n### add\\_script\\_tag\n\ntext\n", "x"), "Page › add_script_tag")
check("…nor in a plain heading", post.display_title("# locator.all\\_inner\\_texts\n\ntext\n", "x"), "locator.all_inner_texts")

# ---- post-processing writes the cleaned titles into the manifest, also when it changes nothing else -----------------------------------------
import asyncio  # noqa: E402


class FakeStore:
    framework_slug = "demo"

    def __init__(self, entries, bodies):
        self._entries, self._bodies = entries, bodies

    @property
    def manifest(self):
        return list(self._entries)

    async def read_body_by_key(self, key):
        return self._bodies[key]


async def post_processing() -> None:
    entities = domains.dd.ingestion.storage.entities
    bodies = {f"k{i}": f"# Page {i}\n\n" + f"Distinct documentation text number {i}. " * 40 for i in range(10)}
    entries = [entities.ManifestEntry(idx=i, slug=f"p{i}", url=f"https://x.dev/docs/p{i}", tier="sitemap", bytes=len(bodies[f"k{i}"]), title=f"Page {i} | Demo Docs", key=f"k{i}")
               for i in range(10)]
    store = FakeStore(entries, bodies)
    summary = await domains.dd.ingestion.post.service.apply_to_store(store)
    check("post-processing takes the site's name off every title, in the manifest that is finalized", ([e.title for e in store.manifest][:3], summary["output_files"]), (["Page 0", "Page 1", "Page 2"], 10))


asyncio.run(post_processing())

print("\nall checks passed")
