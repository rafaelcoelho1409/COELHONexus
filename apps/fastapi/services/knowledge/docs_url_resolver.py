"""
Knowledge Distiller — Docs URL Resolver

Resolves the official documentation root URL for an arbitrary framework name.
Three-layer confirmation so the ingestion waterfall doesn't crawl the wrong
project (the `pydantic-gen.readthedocs.io` vs `docs.pydantic.dev` problem):

  Layer 1 — Hostname tokenization: the framework name must appear as a
            standalone subdomain component (e.g. `pydantic` in
            `docs.pydantic.dev`), NOT as a substring inside a hyphenated
            subdomain (e.g. `pydantic-gen`). Deterministic, free, ~µs.

  Layer 2 — SearXNG title scoring: each search result carries a title.
            Derivative projects usually title themselves differently
            ("pydantic-gen — Python code generator" vs "Pydantic —
            Data validation"). We reward exact framework matches at the
            start of the title. Deterministic, free, ~µs.

  Layer 3 — LLM disambiguation (optional, via `verify=True`): after the
            top-ranked candidate verifies, fetch its homepage, extract
            `<title>` + first paragraph, and ask the scope LLM "Is this
            the official docs for {framework}?". If it says no, try the
            next candidate. ~1-2s, ~500 tokens.

DESIGN: SearXNG is the source of truth. LLM guesses are unreliable (training
cutoffs, hallucinated URLs), and user input is often a typo or the wrong
page. Both are treated as hints, not authorities.

RESOLUTION ORDER:
  1. ALWAYS query SearXNG for "{framework} official documentation"
  2. Score candidates with layers 1+2 (URL + title)
  3. HEAD-verify in rank order
  4. If `verify=True` and an LLM is provided, run layer 3 on the top candidate
  5. User-supplied URL acts as a sanity override when its host matches
     a verified SearXNG candidate (user's specific path wins)
  6. Final fallback: user-supplied URL alone if SearXNG returns nothing

ENV:
    SEARXNG_URL (default: http://searxng.searxng.svc.cluster.local:8080)

TIMEOUTS: 5s per probe; LLM confirmation inherits its own model timeout.
"""
import logging
import os
import re
from typing import Literal
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field


logger = logging.getLogger(__name__)


DocsUrlSource = Literal["searxng", "searxng_user_path", "user_fallback"]


_TIMEOUT_SECONDS = 5.0
_MAX_SEARXNG_CANDIDATES = 10
_MAX_CANDIDATES_TO_LLM_VERIFY = 3  # Don't burn tokens walking the whole list

# Hosts that are never canonical docs, even if well-ranked by search engines.
_BAD_HOSTS = {
    "github.com",       # README != docs root
    "gitlab.com",
    "bitbucket.org",
    "stackoverflow.com",
    "reddit.com",
    "en.wikipedia.org",
    "wikipedia.org",
    "youtube.com",
    "medium.com",
    "dev.to",
    "hashnode.com",
    "substack.com",
    "twitter.com",
    "x.com",
}


def _host_of(url: str) -> str:
    return (urlparse(url).netloc or "").lower()


def _normalize(s: str) -> str:
    """Framework canonical form for matching — lowercase, alnum only."""
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _subdomains(host: str) -> list[str]:
    """Split a host into subdomain components: `docs.pydantic.dev` → ['docs', 'pydantic', 'dev']."""
    return host.lower().split(".")


def _score_candidate(
    url: str,
    title: str,
    framework: str,
    language: str | None,
    version: str | None = None) -> int:
    """
    Score a URL + SearXNG title on "looks like official docs."
    Higher = better. Negative = skip.

    LAYER 1 (hostname tokenization):
      - Strong bonus if framework is a STANDALONE subdomain token
        (`docs.pydantic.dev` → `pydantic` IS standalone → +80)
      - Weaker bonus if framework is the prefix/suffix of a hyphenated
        subdomain (`pydantic-gen.readthedocs.io` → `pydantic-gen` first
        subdomain, `pydantic` is a prefix but hyphenated → +10)
      - Trivial bonus if framework is just a substring somewhere
    LAYER 2 (title scoring):
      - Big bonus if the title starts with the framework name
      - Bonus if the title contains the framework as a word
      - No penalty for derivative titles — we just reward matches
    """
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").lower()
    if host in _BAD_HOSTS:
        return -1000

    fw_norm = _normalize(framework)
    score = 0

    # --- Host-structure heuristics (layer 1a) ---
    if host.startswith("docs."):
        score += 80
    if host.endswith(".readthedocs.io") or host.endswith(".readthedocs.org"):
        score += 70
    if host.startswith("developer.") or host.startswith("developers."):
        score += 50
    if "/docs/" in path or path.endswith("/docs") or path == "/docs":
        score += 40
    if "/documentation" in path:
        score += 30
    if host.endswith(".dev") or host.endswith(".io"):
        score += 15

    # --- Layer 1: framework-token matching in hostname ---
    subs = _subdomains(host)
    # Exact subdomain match — "pydantic" is one of the host parts
    if fw_norm in (_normalize(s) for s in subs):
        score += 80
    else:
        # Is framework a hyphen-bounded token inside any subdomain?
        for sub in subs:
            sub_parts = sub.split("-")
            if fw_norm in (_normalize(p) for p in sub_parts):
                # Standalone token within a hyphenated subdomain.
                # e.g. `pydantic-gen` contains `pydantic` but ALSO `gen`,
                # which strongly suggests a derivative project.
                if len(sub_parts) == 1:
                    score += 60   # just framework, no derivative tag
                else:
                    score += 10   # hyphenated — weaker signal
                break
        else:
            # Last resort: framework as substring somewhere in the compact host
            host_compact = _normalize(host)
            if fw_norm in host_compact:
                score += 5

    # Framework in path adds a smaller nudge
    if fw_norm in _normalize(path):
        score += 10

    # --- Layer 2: SearXNG title scoring ---
    if title:
        t_norm = _normalize(title)
        # Title starts with framework (strong signal: "Pydantic — ..." vs "Using pydantic for...")
        first_word = (title.strip().split() or [""])[0]
        if _normalize(first_word) == fw_norm:
            score += 50
        elif fw_norm in t_norm:
            # Appears somewhere in the title
            score += 20

    # --- Language hint — small nudge ---
    if language:
        lang_l = language.lower()
        if f"/{lang_l}/" in path or lang_l in host:
            score += 10

    # --- Version-awareness: reward URLs that clearly pin the requested version ---
    if version:
        v_norm = _normalize(version)
        # Common patterns: /2.11/, /v2.11.1/, /2.11.1/, /en/2.11/
        # Strip leading "v" and any separators for comparison
        path_variants = [p for p in re.split(r"[/]", path) if p]
        for seg in path_variants:
            seg_clean = _normalize(seg.lstrip("v"))
            if seg_clean == v_norm:
                score += 40              # exact version path match
                break
            elif v_norm.startswith(seg_clean) and len(seg_clean) >= 2:
                # e.g. requested "2.11.1" but URL uses "/2.11/"
                score += 20
                break
        # "latest" is penalty when a specific version was requested
        if "/latest/" in path or path.endswith("/latest"):
            score -= 30

    # Penalize deep paths and query strings — docs roots are usually tidy
    depth = path.count("/")
    if depth > 4:
        score -= 5 * (depth - 4)
    if parsed.query:
        score -= 5
    return score


async def _verify_reachable(
    client: httpx.AsyncClient,
    url: str) -> bool:
    """HEAD (with GET fallback). Follow redirects. Accept 2xx/3xx."""
    try:
        resp = await client.head(url, follow_redirects = True)
        if 200 <= resp.status_code < 400:
            return True
        if resp.status_code != 405:
            return False
    except (httpx.RequestError, httpx.HTTPError):
        pass
    try:
        resp = await client.get(url, follow_redirects = True)
        return 200 <= resp.status_code < 400
    except (httpx.RequestError, httpx.HTTPError) as e:
        logger.info(f"[docs-url-resolver] verify GET failed for {url}: {e}")
        return False


async def _search_searxng(
    client: httpx.AsyncClient,
    searxng_url: str,
    framework: str,
    language: str | None,
    version: str | None = None) -> list[tuple[str, str]]:
    """
    Query SearXNG. Returns [(url, title), ...] in engine order.
    Title is used for layer-2 scoring.

    When `version` is non-empty, it's folded into the query so results
    naturally bias toward version-specific docs (e.g. `pydantic 2.11`
    rather than "pydantic latest").
    """
    parts = [framework, "official documentation"]
    if version:
        parts.insert(1, version)
    if language:
        parts.insert(0, language)
    query = " ".join(parts)
    params = {
        "q": query,
        "format": "json",
        "safesearch": "0",
        "language": "auto",
        "categories": "general",
    }
    endpoint = f"{searxng_url.rstrip('/')}/search"
    try:
        resp = await client.get(endpoint, params = params)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning(f"[docs-url-resolver] SearXNG query failed: {e}")
        return []
    results = data.get("results") or []
    urls: list[tuple[str, str]] = []
    for entry in results[:_MAX_SEARXNG_CANDIDATES]:
        u = entry.get("url")
        t = (entry.get("title") or "").strip()
        if u and u.startswith(("http://", "https://")):
            urls.append((u, t))
    logger.info(
        f"[docs-url-resolver] SearXNG returned {len(urls)} candidates for '{query}'"
    )
    return urls


# =============================================================================
# Layer 3 — LLM disambiguation
# =============================================================================
class DocsUrlConfirmation(BaseModel):
    """LLM's yes/no verdict on whether a page is the official docs for a framework."""
    matches: bool = Field(
        description = (
            "True ONLY if the provided page is the official documentation for the "
            "specified framework (not a derivative project, tutorial, blog, or "
            "package with a confusingly similar name). When in doubt, return False."
        )
    )
    reason: str = Field(
        description = "One-line explanation of the decision, naming what you saw on the page."
    )


_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_BODY_RE = re.compile(r"<body[^>]*>(.*?)</body>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _extract_title_and_text(html: str, max_body_chars: int = 1200) -> tuple[str, str]:
    """Cheap regex-only parse — no BeautifulSoup. Good enough for title + first paragraph."""
    title_match = _TITLE_RE.search(html)
    title = _WS_RE.sub(" ", (title_match.group(1) if title_match else "").strip())[:250]
    body_match = _BODY_RE.search(html)
    body_html = body_match.group(1) if body_match else html
    text = _TAG_RE.sub(" ", body_html)
    text = _WS_RE.sub(" ", text).strip()[:max_body_chars]
    return title, text


async def _llm_confirm_docs_url(
    client: httpx.AsyncClient,
    url: str,
    framework: str,
    llm,
    version: str | None = None) -> tuple[bool, str]:
    """
    Layer 3: fetch the page and ask the LLM whether it's the official docs
    for `framework` (and the requested `version`, if any). Returns
    (matches, reason). On any fetch/LLM error, returns (False, "<error>")
    so the caller falls through to the next candidate safely.
    """
    try:
        resp = await client.get(url, follow_redirects = True)
        resp.raise_for_status()
        html = resp.text[:20000]  # 20KB is plenty for title + first paragraph
    except Exception as e:
        logger.warning(f"[docs-url-resolver] layer-3 fetch failed for {url}: {e}")
        return False, f"fetch failed: {e}"

    title, snippet = _extract_title_and_text(html)
    if not title and not snippet:
        logger.info(f"[docs-url-resolver] layer-3 empty page at {url}")
        return False, "empty page (no title or body)"

    from langchain_core.prompts import ChatPromptTemplate

    version_clause = (
        f"\n- The user explicitly requested version {version!r}. Only return "
        f"matches=True if this page is for that specific version (visible in "
        f"the title, breadcrumb, version-selector, or prominent version "
        f"string). If the page is the 'latest' version or a different pinned "
        f"version, return matches=False."
        if version else ""
    )

    prompt = ChatPromptTemplate.from_messages([
        (
            "system",
            "You verify whether a web page is the OFFICIAL documentation for a "
            "specific framework. Be strict: return matches=True only if the page "
            "is clearly the primary docs site for that framework. Return "
            "matches=False if it's a derivative project (e.g. code generator, "
            "CLI wrapper, tutorial, blog post, or package with a similar name "
            "but different scope)."
            + version_clause +
            "\n\nExamples:\n"
            "- Framework='pydantic', title='Pydantic', body mentions data validation → TRUE\n"
            "- Framework='pydantic', title='pydantic-gen', body mentions code "
            "generation from YAML → FALSE (derivative project)\n"
            "- Framework='fastapi', title='FastAPI', body mentions web framework → TRUE\n"
            "- Framework='fastapi', title='Fast API tutorial blog' → FALSE (blog, not docs)\n"
            "- Framework='pydantic', version='2.11.1', title='Pydantic 2.11 docs' → TRUE\n"
            "- Framework='pydantic', version='1.10', title='Pydantic 2.11 docs' → FALSE (wrong version)"
        ),
        (
            "human",
            "Framework: {framework}\n"
            "Requested version: {version}\n"
            "URL: {url}\n"
            "Page <title>: {title}\n"
            "First content: {snippet}\n\n"
            "Is this the official documentation for {framework}"
            "{version_suffix}?"
        ),
    ])
    chain = prompt | llm.with_structured_output(
        DocsUrlConfirmation,
        method = "function_calling",
    )
    try:
        result: DocsUrlConfirmation = await chain.ainvoke({
            "framework": framework,
            "version": version or "(any — no specific version requested)",
            "url": url,
            "title": title or "(none)",
            "snippet": snippet or "(empty)",
            "version_suffix": f" version {version}" if version else "",
        })
    except Exception as e:
        logger.warning(f"[docs-url-resolver] layer-3 LLM call failed for {url}: {e}")
        return False, f"llm error: {e}"
    logger.info(
        f"[docs-url-resolver] layer-3: {url} (version={version or 'any'}) "
        f"→ matches={result.matches} ({result.reason[:100]})"
    )
    return result.matches, result.reason


# =============================================================================
# Main entry point
# =============================================================================
async def resolve_docs_url(
    framework: str,
    language: str | None,
    user_supplied: str | None,
    version: str | None = None,
    verify: bool = True,
    llm = None,
    searxng_url: str | None = None) -> tuple[str, DocsUrlSource]:
    """
    Return (verified_url, source). Raises RuntimeError if nothing verifies.

    Args:
        framework: user-supplied framework name, non-empty.
        language: optional programming-language hint from ScopeValidation.
        user_supplied: optional user-supplied docs URL (overrides when host matches).
        verify: if True AND `llm` provided, run layer-3 LLM disambiguation on
                each top candidate before accepting. Adds ~1-2s per candidate
                but prevents crawling the wrong project's docs.
        llm:    langchain ChatModel or fallback chain. Required when verify=True.
                The scope classifier is the right choice — fast + small.
        searxng_url: override for env SEARXNG_URL.

    Sources:
        "searxng"            — SearXNG winner (optionally LLM-confirmed)
        "searxng_user_path"  — user_supplied host matched a verified SearXNG
                               candidate; user's specific path preferred
        "user_fallback"      — SearXNG found nothing usable, user URL verified
    """
    searxng_url = searxng_url or os.environ.get(
        "SEARXNG_URL",
        "http://searxng.searxng.svc.cluster.local:8080",
    )

    if verify and llm is None:
        logger.warning(
            "[docs-url-resolver] verify=True but no llm provided — "
            "layer-3 disambiguation will be SKIPPED"
        )

    async with httpx.AsyncClient(
        timeout = httpx.Timeout(_TIMEOUT_SECONDS, connect = _TIMEOUT_SECONDS),
        headers = {"User-Agent": "COELHONexus-KnowledgeDistiller/1.0"},
    ) as client:
        # 1) Ask SearXNG (version-biased query when version is set)
        candidates = await _search_searxng(client, searxng_url, framework, language, version)
        ranked = sorted(
            (
                (_score_candidate(u, t, framework, language, version), u, t)
                for u, t in candidates
            ),
            key = lambda t: t[0],
            reverse = True,
        )
        logger.info(
            f"[docs-url-resolver] searxng ranking (version={version!r}, top 5): "
            + " | ".join(f"{s}:{u}" for s, u, _t in ranked[:5])
        )

        user_host = _host_of(user_supplied) if user_supplied else ""
        llm_checks_done = 0

        # 2) Walk ranked candidates; reachability + optional LLM confirmation
        for score, url, title in ranked:
            if score < 0:
                continue
            if not await _verify_reachable(client, url):
                continue

            # Layer 3: LLM confirmation on the top few reachable candidates
            if verify and llm is not None and llm_checks_done < _MAX_CANDIDATES_TO_LLM_VERIFY:
                matches, reason = await _llm_confirm_docs_url(
                    client, url, framework, llm, version,
                )
                llm_checks_done += 1
                if not matches:
                    logger.info(
                        f"[docs-url-resolver] layer-3 REJECTED {url}: {reason[:120]}"
                    )
                    continue  # try the next candidate

            candidate_host = _host_of(url)
            # User-path override: if user_supplied host matches this verified
            # candidate's host, prefer the user's exact path (respects version
            # or locale intent, e.g. user supplied /v1/).
            if user_host and user_host == candidate_host and user_supplied != url:
                if await _verify_reachable(client, user_supplied):
                    logger.info(
                        f"[docs-url-resolver] SearXNG confirmed host "
                        f"{candidate_host}; preferring user path: {user_supplied}"
                    )
                    return user_supplied, "searxng_user_path"
                logger.info(
                    f"[docs-url-resolver] user URL ({user_supplied}) host matched "
                    f"SearXNG but didn't verify — using SearXNG winner"
                )
            logger.info(
                f"[docs-url-resolver] winner: {url} (score={score}, title={title!r})"
            )
            if user_supplied and user_host and user_host != candidate_host:
                logger.warning(
                    f"[docs-url-resolver] user URL host ({user_host}) disagrees "
                    f"with SearXNG winner ({candidate_host}); using SearXNG"
                )
            return url, "searxng"

        # 3) SearXNG found nothing usable — accept a reachable user URL
        if user_supplied and await _verify_reachable(client, user_supplied):
            logger.warning(
                f"[docs-url-resolver] SearXNG returned no usable candidates; "
                f"falling back to user URL: {user_supplied}"
            )
            return user_supplied, "user_fallback"

    raise RuntimeError(
        f"Could not resolve a reachable docs URL for framework={framework!r} "
        f"(language={language!r}). SearXNG returned no usable candidates, "
        f"LLM rejected {llm_checks_done} top candidates as non-matching, "
        f"and user_supplied={user_supplied!r} did not verify."
    )
