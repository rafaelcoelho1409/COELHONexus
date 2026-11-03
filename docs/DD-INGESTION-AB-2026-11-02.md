# Docs Distiller ingestion — capture, then A/B how it becomes pages

Written 2026-11-02. Status: Tier 1 only; everything uncommitted; needs a server redeploy to take effect (see "Deploy").

## The process

1. **Capture**: a tier keeps what it fetched exactly as it came under `ingestion/raw/{slug}/` — `capture.json` (what, from where, sha256, the catalog's `path_filter`/`nav_url`) and `files/` (`bundle.md`, `llms.txt`). `capture.json` is written last: no `capture.json`, no capture. **All five tiers capture** (2026-11-02, uncommitted, needs a redeploy): each response is stored the moment it arrives (`capture.service.Writer`), named from its URL (`page-<hash of the URL>.<ext>`), HTML/XML gzipped (nginx: 148 pages, 3.2 MB of HTML kept as 0.66 MB), and a request that gave nothing (404, timeout) is listed with `stored: false` so a transform knows the page was tried.

| tier | the capture holds | `params` carries (what the network decided) |
|---|---|---|
| 1 `llms_full` | `bundle.md` (+ `bundle.partN.md`), the site's `llms.txt` | `path_filter`, `nav_url` |
| 2 `llms_txt` | `llms.txt` and every linked page as it came (Markdown, text, `.py`, or HTML) | `path_filter`, `code_language`, `sitemap_url` |
| 3 `sitemap` | every sitemap read (index role), each page's HTML, or its markdown twin (`via: twin`, `for_url` = the page) | `urls` (pages in order), `lastmod`, `twins`, `path_filter`, `language`, `framework_name`, `code_language` |
| 4 `docs` | each page's HTML, the browser's rendering of those it could not read (`page-<hash>.browser.html`, `via: browser`), Sphinx's `objects.inv` bytes | `urls` (crawled, in order), `anchors`, `spa`, `inventory_base`, `path_filter`, `code_language`, `language`, `framework_name` |
| 5 `github` | `tree.json` (the API's tree), each docs file as the repository has it | `org`, `repo`, `branch`, `paths` |

The old `pages/NNNN-slug.md` raw copy is no longer written by any tier (`add_page(keep_raw=False)`), and Synth's normalize backfill no longer seeds it with normalized text. A tier that falls through leaves no capture of its own: the next tier clears the raw prefix before it starts (`Writer.begin`). Checked on real sites with a fake store: FastHTML (Tier 2) 22 files, Terragrunt (Tier 3, twins) 144, nginx (Tier 3, HTML) 150, all complete and hash-checked.
2. **Arm A** (`ab.current`): the tier code as it is, run on a capture. Proven identical to a real Tier 1 run + the post step (body, slug, title as read, section, source URL, tier, order): synthetic scenarios (`scripts/server/test_ingestion_ab.py`) and all 13 real bundles.
3. **Arm B**: whatever you register in `ab.params.ARM_MODULES` — a module with `async def transform(inputs: ab.entities.Inputs) -> list[transform.entities.Page]`. Your agent lives in `apps/server/ai/dd/` (I do not touch it).
4. **Same measures, rule fixed beforehand** (`ab.params.VERDICT`): gates (nothing invented: token precision >= 0.99; nothing lost: recall within 0.02 of A; no code damaged), then three measures (noise per MB, agreement with the site's own `llms.txt`, share of pages <1 KB or >100 KB) and, once a judge exists, its win rate. B wins a source only by clearing the gates and being better on two measures and worse on none; anything else is a tie and keeps A.

```bash
cd apps/server                       # outside the cluster the script maps your MINIO_* shell vars
.venv/bin/python ../../scripts/dd/ab_ingestion.py capture                        # the 13 llms-full sources -> ingestion/raw/ (done 2026-11-02)
.venv/bin/python ../../scripts/dd/ab_ingestion.py run --arm current              # pages + scorecard per source -> /tmp/dd_ab/{slug}/{arm}/
.venv/bin/python ../../scripts/dd/ab_ingestion.py run --arm current --arm agent  # once yours is registered
.venv/bin/python ../../scripts/dd/ab_ingestion.py report --arm agent             # a row per source + the verdict
```

## Arm A on the 13 captures (2026-11-02)

| source | pages | <1 KB | >100 KB | recall | code changed | noise/MB | agrees with index |
|---|---|---|---|---|---|---|---|
| browser-use | 56 | 13 | 0 | .999 | 0/254 | 116 | 1.00 |
| claude-code | 377 | 16 | 9 | .991 | 0/3081 | 3 | .95 |
| dask | 1232 | 218 | 6 | .977 | 9/2685 | 867 | no index |
| dbt | 1045 | 178 | 7 | .999 | 0/4277 | 19 | .81 |
| evidently | 68 | 6 | 0 | .950 | 0/268 | 378 | .96 |
| fastmcp | 359 | 34 | 1 | 1.00 | 0/2150 | 2 | .62 |
| locust | 23 | 1 | 0 | .999 | 0/184 | 64 | 1.00 |
| next-js | 1588 | 444 | 0 | .994 | 24/3099 | 320 | **.10** |
| pydantic | 327 | 100 | 1 | .994 | 1/1338 | 3 | **.27** |
| sglang | 401 | 25 | 1 | 1.00 | 0/3487 | 164 | .99 |
| streamlit | 247 | 24 | 1 | **.909** | 28/869 | 604 | .45 |
| trl | 58 | 1 | 2 | .993 | 0/665 | 121 | .97 |
| unsloth | 201 | 1 | 0 | .969 | 0/1418 | 71 | 1.00 |

## What the captures show (where B has room)

Every bundle delimits its pages differently; A knows `Source:` lines, then H1s, then H2/H3. Seen in the raw files:

- **Next.js**: pages are YAML frontmatter blocks (`---` / `title:` / `url:` / `---`, 476 of them); only 110 H1s exist, mostly index letters and code comments. A cuts on those: 1588 pages for ~300 in the index.
- **Unsloth** (fixed in the capture, not an arm problem): the bundle is paginated — "part 1 of 2 (pages 1-100 of 200) … Next part: …/llms-full.txt/1" — and Tier 1 stored half the docs (101 pages, agreement .50). It now follows `Next part` (same host, at most 25 parts), keeps each part as it came (`bundle.md`, `bundle.part2.md`), joins them without the later banners, and refuses a bundle whose part cannot be had. Re-captured: 201 pages, agreement 1.00. The live server still holds the old 101 until it is redeployed and Unsloth re-ingested.
- **Dask**: every page starts with a file-name H1 (`# index.html.md`) before its real H1: 2425 H1s, 1305 stubs dropped. **dbt**: one H1 over everything, pages as `###` with `---` between them. **Pydantic**: `---` between pages (93) against ~90 in the index; 327 pages.
- **Streamlit**: 403 `Source:` markers become 247 pages (pages without their own H1 merge into the previous one); 59 code blocks differ because `_whitespace_entity_pass` decodes `&gt;` inside fences too (arguably a fix there, a change anywhere else).
- Noise A leaves: Next.js's agent banner on every page ("For an index of all documentation, see llms.txt"), Streamlit's JSX component lines (1196), Dask's HTML layout tags (523), SGLang's (958).

## Brief for arm B

Inputs: `Inputs.text` (the bundle cut to the catalog's `path_filter`), `nav_links` (the site's own index: title, URL, section), `params`. Output: `Page(slug, title, body, section, source_url, tier)`, in manifest order.

Hard limits: the Rotator only (`docs/llm-rotator-backend.md` in AIAgentsStudies; its `auto` model is being removed, use `fgts-va` or `latency-ts`): 64k context, 16 concurrent requests shared with Nexus, no streaming, `max_wall_s` <= 600. The 13 bundles are 47 MB (~12M tokens): an agent that rewrites pages token by token is neither feasible nor safe — the gate punishes any invented or lost word and any changed code block.

Shape I recommend (yours to change): the agent decides, code applies.
1. **Recipe author** (one run per source): looks at the bundle through tools (`peek(start, n)`, `grep(pattern)`, a `try_recipe(recipe)` tool that returns page count, agreement with the index and sample titles) and writes a small structured recipe: how pages are delimited, how titles and URLs are found, which lines are noise. The index is a free oracle it can iterate against — Next.js 0.10 → 0.9 is the first milestone.
2. **A deterministic applier** cuts the bundle with the recipe (code, no LLM) — nothing can be invented.
3. **Critic** (fresh context, like the critic in `docs/math-subagents-architecture.md`): sees a few before/after pages and the scorecard, accepts or sends the author back with reasons. Gates stay in plain code.

Build order, with the chapter that teaches each step:
- M1 — an agent with `peek`/`grep` that says how each of the 13 bundles delimits pages (check it against the list above): chapters 001, 002.
- M2 — the bundle in a file backend instead of the context: chapter 003.
- M3 — the recipe as a structured output + `try_recipe`; get Next.js, Dask, Pydantic, dbt, Unsloth's agreement up: 002, `docs/llm-rotator-backend.md`.
- M4 — noise rules in the recipe, checked by the gates.
- M5 — split author/critic into subagents, per-convention knowledge as skills: 007, 008 (custom subagents do not inherit skills), middleware you may want (retry, summarization): 010.
- M6 — register it in `ab.params.ARM_MODULES`, run both arms, read `report`.

## Not done

- Arm A for tiers 2-5: the harness still runs Tier 1 only (`ab.service.prepare` reads a one-bundle capture). For each of tiers 2-5, `pages_from_capture` (the tier's transform as a pure function: body kind → Markdown, stub and redirect-stub drops, titles, slugs claimed in URL order, then the shared normalize/split/dedup) must be written and proven identical to a live run on real sites (replayed from a capture), and `Inputs` widened from "one text" to "the captured documents" (for HTML sources the fidelity reference is the page's visible text). Decisions taken: image downloads stay a shared stage after the arm; slug collisions are claimed in URL order, not fetch-completion order; HTML is kept gzipped.
- The judge (blind pairwise, a model other than B's; results in `{out}/judge.json` as `{slug: {arm: B's win rate}}`); wiring the winning arm into the live pipeline (image downloads, vaults and the manifest stay a shared stage after any arm).

## Verified live (dev, 2026-11-02)

After the skaffold redeploy a real Tier 1 run of Locust (not ingested before) wrote `ingestion/raw/locust/{capture.json, files/bundle.md, files/llms.txt}`, no `pages/` raw copy, nothing under `ingestion-raw/`; the library lists 36 sources and no "raw". Arm A on that capture equals the pages the server stored in 18 of 23 bodies; the other 5 differ only in image links, which the server downloads and relinks after the arm (the shared stage) and a pure arm does not.

## Deploy

The code moves raw copies to `ingestion/raw/{slug}/` (the folder name `raw` is reserved: `storage.domain.framework_slugs` skips it in the library and the backfill). The live bucket was migrated 2026-11-02 (6,533 objects, sizes verified, `ingestion-raw/` emptied); until the server is redeployed the running code writes `ingestion-raw/` again on the next ingestion.
