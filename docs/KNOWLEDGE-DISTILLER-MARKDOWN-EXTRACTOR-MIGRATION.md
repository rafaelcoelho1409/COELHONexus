# Knowledge Distiller — Markdown Extractor Migration (Trafilatura → Crawl4AI)

Status: pending implementation
Decision date: 2026-04-28
Replaces: trafilatura-based HTML→markdown conversion in Tier 2 / Tier 3
Keeps: trafilatura-free Tier 1 (raw save) and Tier 4 (already on Crawl4AI)

---

## Decision

Replace `trafilatura.extract(html, output_format="markdown", ...)` in the
Tier 2 (`llms.txt`) and Tier 3 (`sitemap.xml`) ingesters with Crawl4AI's
`DefaultMarkdownGenerator` configured for code-preservation. The fetch
step (httpx) stays unchanged — only the in-process HTML→markdown
conversion swaps.

Empirically: trafilatura is news-article-tuned and confirmed weak on
code-block indentation and language-class preservation (issue
[adbar/trafilatura#489](https://github.com/adbar/trafilatura/issues/489)
and corroborating LLM-preprocessing community guidance, April 2026).
Crawl4AI's `DefaultMarkdownGenerator` exposes explicit options
(`mark_code=True`, `handle_code_in_pre=True`) targeting exactly that gap
and is already a dependency in this project.

## Why this is the best option (not Docling, not html-to-markdown-go, not markdownify)

| Option | Code preservation | Speed | Practical fit | Verdict |
|---|---|---|---|---|
| **Crawl4AI `PruningContentFilter` + `DefaultMarkdownGenerator`** | Excellent (`mark_code` / `handle_code_in_pre`) | ~150-300 ms/page (no browser) | Already a dep; same converter T4 uses | **Selected** |
| Docling (IBM) | Excellent on complex layouts | Slow (~1-3 s/page; PyTorch) | Heavy dep, PDF-focused; HTML support less proven | Rejected — dep weight not justified for HTML |
| html-to-markdown (Go, JohannesKaufmann) | Excellent for code | Fast | Subprocess integration brittle; spawn cost dwarfs conversion | Rejected — integration friction |
| markdownify (Python) | Good with code-aware callbacks | Fast | No bundled boilerplate stripper | Rejected — missing pruner stage |
| Trafilatura (current) | Mediocre — indentation drops, language-class drops | Fast (~50 ms/page) | In use today | Replaced |
| html2text | Poor for nested code | Fast | Outdated, less actively maintained | Rejected |

Crawl4AI wins on the constellation: dependency parsimony,
code-preservation primitives, consistency with the T4 path, and acceptable
3-5× slowdown vs trafilatura (well below the 10-20× cost of routing T2/T3
through Playwright).

## Architecture after the swap

| Tier | Fetch | HTML → Markdown |
|---|---|---|
| T1 (`llms_full_txt`) | httpx single GET | n/a — body is already markdown |
| T2 (`llms_txt`) | **httpx (parallel, ~8 concurrent)** | **Crawl4AI `DefaultMarkdownGenerator`** ← change |
| T3 (`sitemap_xml`) | **httpx (parallel, ~10 concurrent)** | **Crawl4AI `DefaultMarkdownGenerator`** ← change |
| T4 (`docs_url`) | Crawl4AI Playwright (JS-rendered) | Crawl4AI `DefaultMarkdownGenerator` (already) |
| T-GH (`readme_only`) | httpx + GitHub API | n/a — raw `.md` files |

Fetch stays with httpx for T2/T3 because:
- 50-200 ms/page vs Playwright's 3-5 s/page
- Concurrency-friendly (Playwright stalls > 4 sessions on shared CDP per
  Crawl4AI issues #1326, #1927)
- No memory pressure (Playwright tabs are 200-500 MB each)
- Sites that publish a static llms.txt or sitemap are by construction
  not JS-only — they self-report static content via the very files we read

## Implementation

Files to edit:
- `apps/fastapi/services/knowledge/llms_txt_ingest.py::_html_to_markdown`
- `apps/fastapi/services/knowledge/sitemap_ingest.py::_extract_markdown`

Module-level singletons (built once, reused per page):

```python
from crawl4ai.content_filter_strategy import PruningContentFilter
from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

_PRUNER = PruningContentFilter(
    threshold = 0.48,
    threshold_type = "dynamic",
    min_word_threshold = 10,
)
_MD_GEN = DefaultMarkdownGenerator(
    content_filter = _PRUNER,
    options = {
        "mark_code": True,            # explicit fenced-block detection from <pre><code>
        "handle_code_in_pre": True,   # preserve indentation in <pre> blocks
        "body_width": 0,              # no line wrapping → keeps long code lines intact
        "escape_html": False,         # don't double-escape inline HTML in code
        "wrap": False,                # no auto-wrap of paragraphs
    },
)
```

Per-page conversion (drop-in replacement for `trafilatura.extract`):

```python
def _html_to_markdown(html: str, url: str) -> Optional[str]:
    if not html or not html.strip():
        return None
    try:
        result = _MD_GEN.generate_markdown(html, url = url)
        md = result.fit_markdown or result.raw_markdown
        return md if (md and md.strip()) else None
    except Exception as e:
        logger.warning(f"[md-gen] {url} failed: {e}")
        return None
```

The same function body lives in both T2 (`llms_txt_ingest.py`) and T3
(`sitemap_ingest.py`). The pruner+generator instances can be hoisted to a
shared helper if duplication becomes painful.

After the swap:
- Drop `trafilatura` from `apps/fastapi/pyproject.toml` if no other
  consumer remains (verify with `grep -rn 'trafilatura' --include='*.py'`).

## PruningContentFilter threshold tuning

The default `threshold=0.48` is tuned for blog-style content. For docs
sites with high link density (API references, sidebar nav menus), it can
over-prune legitimate content. Validation procedure:

1. After deploy, re-run **MLflow** (T2) and **Helm** (T3) ingestions —
   same corpora as the pre-swap baseline tests.
2. Compare `total_files` and a sample of generated `.md` files against
   the trafilatura baseline. Specifically check:
   - Code-block fence balance (count of triple-backticks should be even)
   - Language tags preserved (`\`\`\`python`, `\`\`\`bash`)
   - API method tables / sidebar lists not dropped
3. If legitimate content gets stripped:
   - Lower `threshold` to `0.40` or `0.35` (less aggressive pruning)
   - Or set `threshold_type="fixed"` and adjust manually
4. If too much chrome remains (nav, footer, cookie banners):
   - Raise `threshold` to `0.55` (more aggressive)

This is one number to tune per redeploy, not an architectural decision.

## A/B validation plan

After redeploy:

| Framework | Tier | Baseline (trafilatura) | New (Crawl4AI) | Compare |
|---|---|---|---|---|
| MLflow | T2 | 54 files / 692 KB / ~85s | TBD | Code blocks, table structure, fence balance |
| Helm | T3 | 339 files / 1.58 MB / ~3 min | TBD | Code blocks, sidebar preservation, file count |

Sample 5-10 random pages per framework and visually diff the markdown.
Acceptance: Crawl4AI output preserves more code blocks AND keeps file
count within ±10% of baseline (a large drop signals pruner is too
aggressive; a large gain signals chrome is leaking through).

## Risks + mitigations

| Risk | Mitigation |
|---|---|
| Pruner too aggressive on docs with link-heavy sidebars | Lower `threshold` per validation; per-framework override possible via cfg if needed |
| `DefaultMarkdownGenerator` unstable on raw httpx HTML (designed for Crawl4AI's filtered_html) | Wrap in try/except + log; fall back to raw body save if conversion fails |
| 3-5× slower than trafilatura | Acceptable per `feedback_kd_quality_over_speed` — wall-time isn't the priority |
| Crawl4AI breaking changes between versions | Pin Crawl4AI in pyproject.toml; monitor changelog for `markdown_generation_strategy` API shifts |

## Out of scope

- Not changing fetch infrastructure (httpx stays for T2/T3)
- Not switching T1 path (already raw markdown save)
- Not switching T4 path (already on Crawl4AI)
- Not adopting Docling, html-to-markdown (Go), or markdownify
- Not changing the `_passes_content_quality` quality gate (`min_page_chars`,
  `max_link_text_ratio`)
- Not addressing the cache layer's T1/T2/T3 raw-files-not-cached gap
  (separate concern — see future cache-write-during-streaming work)

## References

- Crawl4AI Markdown Generation: https://docs.crawl4ai.com/core/markdown-generation/
- Crawl4AI Fit Markdown: https://docs.crawl4ai.com/core/fit-markdown/
- Crawl4AI repo: https://github.com/unclecode/crawl4ai
- Trafilatura code-block known issue (#489): https://github.com/adbar/trafilatura/issues/489
- LLM HTML preprocessing community guidance: https://dev.to/rosgluk/html-preprocessing-for-llms-3mk8
- Comparison overview (April 2026): https://www.glukhov.org/post/2025/10/convert-html-to-markdown-in-python/
- Crawl4AI issue #1326 (CDP race conditions on >4 sessions): https://github.com/unclecode/crawl4ai/issues/1326
- Crawl4AI issue #1927 (BFS dispatcher ignores max_session_permit): https://github.com/unclecode/crawl4ai/issues/1927
