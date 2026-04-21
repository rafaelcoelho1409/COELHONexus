# Crawler Optimization — Hybrid Crawl4AI + rs-trafilatura + httpx

Research date: 2026-04-21
Status: **Researched, NOT YET APPLIED**

## TL;DR

After independent, skeptical research across Python, Go, and Rust ecosystems, the best path forward is a **surgical hybrid**, not a migration. Keep Crawl4AI + Playwright CDP; swap the markdown extractor to `rs-trafilatura`; add an `httpx` fast-path for static-SSR sites.

Expected gains: **+19.5 F1 on doc-site content extraction**, **4-8× throughput** on static-SSR targets.

## Rejected alternatives (with independent evidence)

| Option | Why rejected |
|---|---|
| **spider-rs** Python binding | Pre-1.0 (v0.0.57 since Jan 2025), macOS-only wheels on PyPI, `website.stop()` bug open since Apr 2025, benchmarks vendor-authored by Spider's CEO, Crawl4AI tested without proxies on bot-protected sites irrelevant to our workload |
| **Go colly + chromedp** | No independent benchmark on doc sites in 2025-2026; migration = ~1500 LOC rewrite, no measurable quality upside |
| **Scrapy + scrapy-playwright** | Still needs separate content extractor; rewrite cost without quality gain |
| **Firecrawl self-host** | AGPL-3.0 license; [Zyte Dec 2025 benchmark](https://www.zyte.com/blog/best-web-scraping-apis/) rates it "excellent crawler, poor unblocking API"; Proxyway late-2025 benchmark reports 33.69% success at 2 req/s; [r/n8n Jun 2025 thread](https://www.reddit.com/r/n8n/) explicitly recommends Crawl4AI over Firecrawl for documentation |
| **Crawl4AI `AsyncHTTPCrawlerStrategy`** | Known broken: [issue #794](https://github.com/unclecode/crawl4ai/issues/794) crashes on `arun_many` with >2 URLs; [issue #1894](https://github.com/unclecode/crawl4ai/issues/1894) open on v0.8.0 — `page_timeout` passed in ms but aiohttp expects seconds, making timeouts 1000× too long |
| **Docrawl** | Could not verify existence as real stable project; appears hallucinated in prior research |
| **Multiple local Chromium instances** | Crawl4AI v0.8 docs state "the crawler still uses one browser" when parallelizing — N processes = N launch costs, not a throughput win on SSR |

## Gold-standard evidence for the recommendation

**Foley, M. (2026). Web Content Extraction Benchmark (WCXB).**
- DOI: [10.5281/zenodo.19316874](https://doi.org/10.5281/zenodo.19316874)
- License: CC-BY-4.0
- Scale: 2,008 pages, 7 page types, 1,613 domains
- Methodology: held-out test set, F1 on extracted main content

On the **Documentation** subset (133 pages):

| Extractor | F1 | Notes |
|---|---|---|
| **rs-trafilatura** | **0.931** | Rust, PyO3 Python wheel |
| Trafilatura (Python) | 0.888 | Mature Python library |
| MinerU-HTML (0.6B model) | 0.838 | ML-based extraction |
| Readability-style | 0.736 | What Crawl4AI's `PruningContentFilter` approximates |

**+19.5 F1 over Readability-style on doc sites.** This is the single piece of independent, DOI-backed evidence that isn't vendor-authored.

## Concrete integration sketch

### 1. Custom MarkdownGenerationStrategy using rs-trafilatura

```python
# apps/fastapi/services/knowledge/rs_trafilatura_markdown.py
from crawl4ai.markdown_generation_strategy import MarkdownGenerationStrategy
import rs_trafilatura

class RSTrafilaturaMarkdown(MarkdownGenerationStrategy):
    def generate(self, cleaned_html: str, url: str | None = None, **_) -> str:
        result = rs_trafilatura.extract(
            cleaned_html,
            url=url,
            output_format="markdown",
            include_links=True,
            include_tables=True,
            favor_recall=True,
        )
        return result.main_content or ""

# fallback to DefaultMarkdownGenerator if rs-trafilatura returns <200 chars
```

Wire it into `ingestion.py`:
```python
from services.knowledge.rs_trafilatura_markdown import RSTrafilaturaMarkdown

_md_generator = RSTrafilaturaMarkdown()  # was DefaultMarkdownGenerator(...)
```

### 2. httpx static-SSR fast-path

```python
# Pre-check: skip Playwright entirely for static-SSR sites
async def _try_static_fast_path(url: str, timeout: int = 10) -> str | None:
    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
        resp = await client.get(url, headers={"User-Agent": "..."})
        if resp.status_code != 200:
            return None
        html = resp.text
        # Crude SPA detection — look for client-rendered markers
        if "__NEXT_DATA__" in html and "<main" not in html.lower():
            return None  # SPA, needs browser
        if len(html) < 500:
            return None  # placeholder / error page
        return html
```

Apply at the ingest level: if seed page passes the check, use httpx for all URLs in that site. Otherwise fall through to Playwright CDP.

### 3. Fallback strategy

- rs-trafilatura returns <200 chars → use `DefaultMarkdownGenerator` fallback
- httpx returns non-200 or suspicious HTML → fall through to Playwright
- Playwright timeout on a page → existing retry pass handles it

## Expected gains (evidence-based)

| Metric | Current | After hybrid | Method |
|---|---|---|---|
| Doc-extraction F1 | ~0.74 (Readability-style) | **0.931** | Direct WCXB benchmark measurement |
| RAG recall@5 | ~84% | **~89-91%** | Extrapolation: +0.19 F1 → +5-7 pts recall per [Bevendorff SIGIR 2023](https://github.com/chatnoir-eu/web-content-extraction-benchmark) |
| Static-SSR throughput | 0.37 pg/s | **1.5-3 pg/s/worker × 4 = 6-12 pg/s** | rs-trafilatura 44ms/page + typical httpx RTT ~200ms |
| SPA throughput | 0.37 pg/s | unchanged | Still Playwright |

## Integration complexity

- **~150 LOC** added (custom MarkdownGenerationStrategy + static-vs-dynamic router)
- **One new dependency:** `rs-trafilatura` (PyO3 wheel, no Rust toolchain on user)
- **Zero new infrastructure** — stays in-process, no microservice
- **Pydantic schema unchanged** — output is still markdown, same downstream consumers
- **Reversible in one git revert**

## Risks + mitigations

| Risk | Mitigation |
|---|---|
| rs-trafilatura is a side project, single maintainer | Pin a version, contribute back upstream if issues, keep PruningContentFilter as fallback |
| WCXB Docs ground truth is only 133 pages | Validate on 100 hand-annotated LangChain pages before cutover |
| rs-trafilatura's code blocks + tables "need another pass" per author | Run A/B on LangChain corpus (heavy on code); keep per-site flag to revert to PruningContentFilter |
| Static-path heuristic misfires on Docusaurus sites with lazy TOCs | Retry through CDP if extraction returns <200 chars |
| User's 0.37 pg/s might be network-bound, not extractor-bound | **MEASURE FIRST**: run 100 URLs through httpx + rs-trafilatura synchronously; if <2 pg/s/worker, extractor change alone won't solve throughput |

## Validation protocol (before cutover)

1. `pip install rs-trafilatura`
2. Pick 100 random URLs from `reference.langchain.com/python/deepagents`
3. Run two extractors side-by-side (rs-trafilatura vs current DefaultMarkdownGenerator+PruningContentFilter)
4. Diff:
   - Markdown byte count (±5% tolerance)
   - Keyword recall: 95%+ of substantive HTML terms appear in markdown
   - Visual diff on 10 samples
5. If green → feature-flag the rollout (`INGESTION_MD_ENGINE=rs_trafilatura|default`)
6. 1-week shadow run + diff metrics in production
7. Flip default

## What's NOT worth doing (confirmed)

- Full migration to Go or Rust
- Replacing Crawl4AI's orchestration (dispatcher, BFS, filters, session_id)
- Adopting Firecrawl (self-host or cloud)
- Using Crawl4AI's `AsyncHTTPCrawlerStrategy` (broken)

## Sources (independent, non-vendor)

1. [WCXB benchmark, Foley 2026](https://doi.org/10.5281/zenodo.19316874) — **gold standard**, rs-trafilatura F1=0.931 on Docs
2. [Crawl4AI issue #794](https://github.com/unclecode/crawl4ai/issues/794) — AsyncHTTPCrawlerStrategy broken for arun_many
3. [Crawl4AI issue #1894](https://github.com/unclecode/crawl4ai/issues/1894) — page_timeout unit bug, open on v0.8.0
4. [Bevendorff et al., SIGIR 2023](https://github.com/chatnoir-eu/web-content-extraction-benchmark) — foundational: extraction quality is page-type-dependent
5. [Zyte "Best Web Scraping APIs 2026"](https://www.zyte.com/blog/best-web-scraping-apis/) — independent Firecrawl review
6. [r/n8n community thread June 2025](https://www.reddit.com/r/n8n/) — production users prefer Crawl4AI for documentation

## Explicitly flagged as BIASED evidence (rejected for this decision)

- [spider.cloud "Honest Benchmark"](https://spider.cloud/blog/firecrawl-vs-crawl4ai-vs-spider-honest-benchmark) — self-disclosed vendor-authored; Crawl4AI tested without proxies on anti-bot sites irrelevant to docs workload
- All firecrawl.dev listicles — vendor content marketing
- Thunderbit, Apify blogs — SEO content with no methodology

## Decision matrix

| Path | Action |
|---|---|
| **Short-term (now):** Keep current stack as-is, measure baseline accurately | ✅ Already doing this |
| **Short-term (1-2 weeks):** Apply the hybrid (rs-trafilatura + httpx fast-path) behind feature flag | **← recommended when ready** |
| **Medium-term:** After validation, flip default, monitor for a month | |
| **Long-term (6+ months):** Revisit if throughput >100 pg/s is needed (not current requirement) | Not urgent |

## Files that would be edited (when applying)

- `apps/fastapi/services/knowledge/ingestion.py` — wire in new MarkdownGenerationStrategy + static-path router
- New file: `apps/fastapi/services/knowledge/rs_trafilatura_markdown.py` — custom strategy wrapper
- `apps/fastapi/requirements.txt` — add `rs-trafilatura`
- `apps/fastapi/Dockerfile.fastapi` — no changes (rs-trafilatura ships as manylinux wheel)
