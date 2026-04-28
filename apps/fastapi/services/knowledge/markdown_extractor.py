"""
HTML → Markdown extractor — Crawl4AI's DefaultMarkdownGenerator (raw_markdown).

Replaces trafilatura for Tier 2 (`llms.txt`) and Tier 3 (`sitemap.xml`)
ingesters. Same converter T4 already uses, so all four tiers produce
markdown the same way.

DESIGN: NO content filter (no pruner). Returns `raw_markdown` directly.

Rationale (2026-04-28 follow-up after empirical testing):
  - PruningContentFilter has known issues stripping legitimate code blocks
    on code-heavy reference pages (Crawl4AI issues #325, #582). Empirical:
    14% of MLflow's pages came back with ZERO code fences, including a
    REST API reference page (45 KB body, 0 code) and stub-stripped Python
    and Java API pages (134-356 chars vs original ~5-20 KB).
  - Crawl4AI's own docs prescribe omitting `content_filter` to disable
    pruning while keeping the markdown generation features:
      "raw_markdown = direct HTML-to-markdown transformation (no filtering)"
      "To generate markdown without filtering, omit the content_filter"
  - Code-block preservation > chrome-stripping for synthesis quality.
    Code is irrecoverable; chrome is filterable downstream.
  - Trade: ~10-30% larger corpus per page (more chrome leaks through),
    but the synthesizer's prompts already focus on technical content and
    can ignore obvious nav/footer noise.

Code-preserving conversion options retained (`mark_code`,
`handle_code_in_pre`, `body_width=0`, `escape_html=False`, `wrap=False`).

See docs/KNOWLEDGE-DISTILLER-MARKDOWN-EXTRACTOR-MIGRATION.md for the
broader decision rationale (trafilatura → Crawl4AI) and the empirical
testing that drove the no-pruner choice.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


# Singleton — built once on first import. DefaultMarkdownGenerator is
# stateless; sharing across pages amortizes init cost over a run.
_MD_GEN = None


def _ensure_initialized() -> None:
    """Lazy import + init. Defer Crawl4AI's heavy dependency chain
    until first conversion is actually requested."""
    global _MD_GEN
    if _MD_GEN is not None:
        return
    from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

    # No `content_filter` — pruning disabled. The markdown produced is
    # `raw_markdown` (direct HTML→md transform; all content preserved
    # including code blocks, sidebars, tables). Code-block formatting
    # options retained.
    _MD_GEN = DefaultMarkdownGenerator(
        options = {
            "mark_code": True,            # explicit fenced-block detection
            "handle_code_in_pre": True,   # preserve indentation in <pre>
            "body_width": 0,              # no line wrapping (preserves long code lines)
            "escape_html": False,         # don't double-escape HTML in code
            "wrap": False,                # no auto-wrap of paragraphs
            "ignore_links": False,        # keep cross-references
        },
    )


def html_to_markdown(html: str, url: str) -> Optional[str]:
    """
    Convert HTML to LLM-ready markdown using Crawl4AI's pipeline (no pruner).

    Returns None on empty input, empty extraction, or any conversion
    failure — caller should treat None as a soft-fail (log + skip page).

    Always returns `raw_markdown` (direct HTML→md transform, no pruning).
    Code blocks, sidebars, navigation chrome all preserved. Downstream
    synthesis is responsible for filtering chrome via prompt engineering.
    """
    if not html or not html.strip():
        return None
    try:
        _ensure_initialized()
    except Exception as e:
        logger.warning(f"[md-gen] init failed: {e}")
        return None
    try:
        result = _MD_GEN.generate_markdown(html, base_url = url)
    except Exception as e:
        logger.warning(f"[md-gen] convert failed for {url}: {e}")
        return None
    md = getattr(result, "raw_markdown", None)
    return md if (md and md.strip()) else None
