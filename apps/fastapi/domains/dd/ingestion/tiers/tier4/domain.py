"""Tier 4 — pure helpers (no I/O): slugify, seeder glob pattern, link
extraction from an already-fetched HTML string, SPA-shell heuristic, and
Playwright run-config assembly from already-constructed objects."""
from __future__ import annotations
from . import params, patterns

import re
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup



def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:80] or "page"


def seed_pattern_for(path: str) -> Optional[str]:
    """Subtree path → seeder glob. Bare root → None (full-domain search)."""
    cleaned = (path or "").rstrip("/")
    if not cleaned or cleaned in ("/", ""):
        return None
    return f"*{cleaned}*"


def extract_links(html: str, base_url: str) -> list[str]:
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return []
    out: list[str] = []
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:", "data:")):
            continue
        full = urljoin(base_url, href)
        if not full.startswith(("http://", "https://")):
            continue
        out.append(full.split("#", 1)[0])
    return out


def looks_like_spa_shell(body: str) -> bool:
    if not body or len(body) < params.SPA_BODY_MIN:
        return True
    no_script = re.sub(
        r"<script[^>]*>.*?</script>", "", body,
        flags=re.DOTALL | re.IGNORECASE,
    )
    no_style = re.sub(
        r"<style[^>]*>.*?</style>", "", no_script,
        flags=re.DOTALL | re.IGNORECASE,
    )
    visible = re.sub(r"<[^>]+>", " ", no_style)
    if len(visible.strip()) < params.SPA_TEXT_MIN:
        return True
    if patterns.SPA_ROOT_RE.search(body):
        return True
    if patterns.HYDRATED_SPA_RE.search(body):
        return True
    return False


def build_run_configs(cfg_cls, cache_mode, lxml_strategy, md_generator):
    common = dict(
        cache_mode = cache_mode.BYPASS,
        wait_until = "domcontentloaded",
        wait_for = (
            "js:() => document.readyState === 'complete' && "
            "!!document.querySelector('#__next, main, article')"
        ),
        word_count_threshold = 50,
        excluded_tags = ["nav", "footer", "aside"],
        exclude_external_links = True,
        scraping_strategy = lxml_strategy,
        markdown_generator = md_generator,
        stream = True,
        max_retries = 2,
        verbose = False,
    )
    primary = cfg_cls(
        **common,
        delay_before_return_html = 0.2,
        page_timeout = params.PAGE_TIMEOUT_MS,
    )
    retry = cfg_cls(
        **common,
        delay_before_return_html = 2.0,
        page_timeout = params.RETRY_PAGE_TIMEOUT_MS,
    )
    return primary, retry
