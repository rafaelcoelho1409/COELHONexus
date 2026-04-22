"""
Knowledge Distiller — Docs Resolver (orchestrator)

Four-stage pipeline per topic:

  A — Registry hint       (services.knowledge.registry.hint_lookup)
  B — Search candidates   (services.search_chain.search_candidates)
                          — multi-provider fallback: Exa → Tavily → Jina
  C — LLM rerank          (this module)
  D — Validator           (services.knowledge.docs_probe.probe_and_classify)

Plus a pre-pass crossover decomposer (services.knowledge.crossover.decompose)
that splits combined-study requests like "DeepAgents + LangChain + LangGraph"
into canonical topics, then fans out Stages A-D per topic via asyncio.gather.

Caching:
  Redis key  = coelhonexus:resolver:{sha256(canonical_name|aliases|version)}
  TTL        = 7 days on confidence ≥ 0.7, 1 hour on lower (retry sooner)
  Invalidate = force_refresh=True on the request

Reference: docs/KNOWLEDGE-DISTILLER-RESOLVER-STRATEGY.md
"""
import asyncio
import hashlib
import json
import logging
from collections import OrderedDict
from typing import Optional
from urllib.parse import urlparse

from langchain_openai import ChatOpenAI

from schemas.knowledge.prompts import RESOLVER_RERANK_PROMPT
from services.knowledge.canonical_url import normalize_candidates
from schemas.knowledge.resolver import (
    DecompositionTopic,
    LLMRerankOutput,
    RegistryHint,
    ResolveRequest,
    ResolvedDocs,
    ResolvedStudy,
    SearchHit,
    Tier,
    TierEvidence,
    TierProbe,
)
from services.knowledge.crossover import decompose
from services.knowledge.docs_probe import probe_and_classify
from services.knowledge.registry import hint_lookup as registry_hint_lookup
from services.search_chain import SearchFallbackChain, search_candidates


logger = logging.getLogger(__name__)


_CACHE_PREFIX = "coelhonexus:resolver:"
_TTL_HIGH_CONFIDENCE = 7 * 24 * 60 * 60   # 7 days on conf ≥ 0.7
_TTL_LOW_CONFIDENCE = 60 * 60             # 1 hour otherwise
_HIGH_CONFIDENCE_THRESHOLD = 0.7
_MIN_ACCEPTABLE_CONFIDENCE = 0.3


# =============================================================================
# Cache helpers
# =============================================================================
def _cache_key(canonical_name: str, aliases: list[str], version: str | None) -> str:
    """
    Stable hash of the identity tuple. Aliases are sorted so different order
    of the same set produces the same key.
    """
    material = json.dumps(
        {
            "name": canonical_name.strip().lower(),
            "aliases": sorted(a.strip().lower() for a in aliases),
            "version": (version or "").strip().lower() or "latest",
        },
        sort_keys = True,
    )
    h = hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]
    return f"{_CACHE_PREFIX}{h}"


async def _cache_get(redis_aio, key: str) -> Optional[ResolvedDocs]:
    raw = await redis_aio.get(key)
    if not raw:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    try:
        return ResolvedDocs(**json.loads(raw))
    except Exception as e:
        logger.warning(f"[resolver] cache deserialize failed for {key}: {e}")
        return None


async def _cache_set(redis_aio, key: str, value: ResolvedDocs) -> None:
    ttl = (
        _TTL_HIGH_CONFIDENCE
        if value.confidence >= _HIGH_CONFIDENCE_THRESHOLD
        else _TTL_LOW_CONFIDENCE
    )
    await redis_aio.set(key, value.model_dump_json(), ex = ttl)


# =============================================================================
# Stage C — LLM rerank
# =============================================================================
def _escape_braces(s: str) -> str:
    """
    LangChain's ChatPromptTemplate treats '{name}' as a variable. User-
    supplied content (search titles/snippets, registry homepages) can
    legitimately contain braces — escape them to double-braces so the
    template engine sees literals.
    """
    return s.replace("{", "{{").replace("}", "}}")


def _format_candidates_block(hits: list[SearchHit]) -> str:
    if not hits:
        return "(no search candidates — resolver must pick from registry hint only)"
    lines = []
    for i, h in enumerate(hits, 1):
        title = _escape_braces((h.title or "(no title)")[:180])
        snippet = _escape_braces((h.snippet or "(no snippet)")[:200])
        url = _escape_braces(h.url)
        lines.append(f"{i}. {url}\n   title: {title}\n   snippet: {snippet}")
    return "\n".join(lines)


def _format_registry_hint(hint: RegistryHint) -> str:
    if not hint.exists:
        return "(package not found in any registry)"
    parts = [f"source: {hint.source or '?'}"]
    if hint.homepage:
        parts.append(f"homepage: {_escape_braces(hint.homepage)}")
    if hint.repo:
        parts.append(f"repo: {_escape_braces(hint.repo)}")
    if hint.latest_version:
        parts.append(f"latest_version: {_escape_braces(hint.latest_version)}")
    return "\n".join(parts)


async def _llm_rerank(
    framework: str,
    aliases: list[str],
    version: str | None,
    registry_hint: RegistryHint,
    hits: list[SearchHit],
    llm: ChatOpenAI) -> LLMRerankOutput:
    """
    Strict-schema LLM pass. Picks the canonical docs_url from candidates.
    Never invents URLs — `with_structured_output` + the prompt's RULES #1
    enforce this.
    """
    chain = RESOLVER_RERANK_PROMPT | llm.with_structured_output(
        LLMRerankOutput,
        method = "function_calling",
    )
    result = await chain.ainvoke({
        "framework": framework,
        "aliases": (", ".join(aliases) or "(none)"),
        "version": version or "latest",
        "registry_hint": _format_registry_hint(registry_hint),
        "candidates_block": _format_candidates_block(hits),
    })
    return result


# =============================================================================
# Single-topic pipeline (Stages A → B → C → D)
# =============================================================================
async def _resolve_topic(
    topic: DecompositionTopic,
    version: str | None,
    aliases: list[str],
    llm: ChatOpenAI,
    search_chain: SearchFallbackChain,
    allow_fallback: bool,
    redis_aio,
    force_refresh: bool) -> ResolvedDocs:
    """
    Run the per-topic Stages A-D pipeline. Returns a ResolvedDocs. On
    recoverable failures (search empty, LLM timeout, etc.) this function
    degrades gracefully — see resolver design doc § "Error handling".
    """
    canonical = topic.canonical_name
    key = _cache_key(canonical, aliases, version)

    # Cache fast-path
    if not force_refresh:
        cached = await _cache_get(redis_aio, key)
        if cached:
            logger.info(f"[resolver] cache hit for {canonical!r}")
            return cached

    # Stage A — Registry (existence + homepage/repo hints)
    #    We DON'T pass a language hint here since the crossover decomposer
    #    doesn't surface one. hint_lookup probes PyPI+npm+crates until hit.
    stage_a_task = asyncio.create_task(
        registry_hint_lookup(canonical, language = None),
    )

    # Stage B — Search (multi-provider fallback: Exa → Tavily → Jina)
    stage_b_task = asyncio.create_task(
        search_candidates(search_chain, canonical, aliases = aliases, version = version),
    )

    registry_hint, hits = await asyncio.gather(stage_a_task, stage_b_task)

    # Stage B+ — Canonical URL normalization. Follow 301/302 redirects and
    # parse <link rel="canonical"> on each hit so legacy / alias docs hosts
    # collapse onto the publisher-declared URL before the LLM sees them.
    # Fixes the LangChain case where search returns python.langchain.com
    # (legacy) alongside docs.langchain.com (canonical) — the former 301s to
    # the latter, but only if we follow redirects. See canonical_url.py.
    if hits:
        try:
            hits = await normalize_candidates(hits)
        except Exception as e:
            # Normalization is best-effort — on any unexpected failure,
            # fall through with the original hits rather than blocking the
            # whole resolve.
            logger.warning(
                f"[resolver] canonical normalization failed for {canonical!r}: "
                f"{type(e).__name__}: {e} — continuing with raw hits"
            )

    # Stage C — LLM rerank
    rerank: Optional[LLMRerankOutput] = None
    rerank_error: Optional[str] = None
    if hits or registry_hint.homepage:
        try:
            rerank = await _llm_rerank(
                framework = canonical,
                aliases = aliases,
                version = version,
                registry_hint = registry_hint,
                hits = hits,
                llm = llm,
            )
        except Exception as e:
            rerank_error = f"{type(e).__name__}: {e}"
            logger.warning(
                f"[resolver] LLM rerank failed for {canonical!r}: {rerank_error}"
            )

    # If LLM rerank succeeded, use its pick. Otherwise degrade to registry.homepage.
    if rerank and rerank.docs_url:
        docs_url = rerank.docs_url
        repo_url = rerank.repo_url
        registry_url = rerank.registry_url
        resolved_name = rerank.canonical_name
        confidence = rerank.confidence
        fallback_candidates = rerank.rejected
    else:
        # Degraded path — registry homepage as last-resort docs_url
        docs_url = registry_hint.homepage
        repo_url = registry_hint.repo
        registry_url = None
        resolved_name = canonical
        confidence = 0.4 if docs_url else 0.0
        fallback_candidates = [h.url for h in hits[:5]]

    # Stage D — Validator (content-validated probes → tier classification,
    # plus D0 root liveness + D2 index spot-check). probe_and_classify also
    # runs GitHub-host discovery: if LLM picked a github.com URL, the probe
    # may re-resolve to the repo's declared homepage or GH-Pages site before
    # classifying.
    tier: Tier = 4
    tier_evidence: TierEvidence
    discovery_meta: dict = {}
    if docs_url:
        tier, tier_evidence, discovery_meta = await probe_and_classify(docs_url)
        # If discovery upgraded the URL (github homepage / has_pages), the
        # crawler should see the upgraded URL — not the original github one.
        upgraded = discovery_meta.get("github_discover")
        if upgraded == "homepage" and discovery_meta.get("homepage"):
            docs_url = discovery_meta["homepage"]
        elif upgraded == "pages":
            org = discovery_meta.get("org")
            repo = discovery_meta.get("repo")
            if org and repo:
                docs_url = f"https://{org}.github.io/{repo}"
    else:
        # No docs_url at all — synthesize an ERROR-state evidence bundle
        placeholder = TierProbe(
            url = "(none)",
            result = "ERROR",
            reason = "no docs_url resolved",
            bytes_read = 0,
        )
        tier_evidence = TierEvidence(
            llms_full_txt = placeholder,
            llms_txt = placeholder,
            sitemap_xml = placeholder,
        )

    # D0 liveness reflects into confidence + docs_url nulling. A DEAD root
    # (404 / off-host redirect) or PARKED root means no crawler can recover
    # content from docs_url — null it out so the caller surfaces fallbacks.
    liveness = tier_evidence.root_liveness
    if liveness:
        if liveness.status == "DEAD":
            logger.info(f"[resolver] {canonical!r}: D0 DEAD — nulling docs_url")
            docs_url = None
            confidence = min(confidence, 0.15)
        elif liveness.status == "PARKED":
            logger.info(f"[resolver] {canonical!r}: D0 PARKED — nulling docs_url")
            docs_url = None
            confidence = 0.0
        elif liveness.status == "EMPTY_SHELL":
            # Site reachable but no docs-site signals — soft penalty.
            confidence = max(0.0, confidence - 0.25)
        elif liveness.status == "ERROR":
            confidence = max(0.0, confidence - 0.15)

    # D2 spot-check penalty — applied separately from the downgrade the
    # probe already did. A downgrade means the tier dropped; we also nudge
    # confidence down so the caller sees the uncertainty.
    spot = tier_evidence.spot_check
    if spot and spot.downgrade_applied:
        confidence = max(0.0, confidence - 0.2)

    # Penalize confidence slightly on tier 4 (harder crawl target, more likely wrong)
    if tier == 4 and confidence > 0.0:
        confidence = max(0.0, confidence - 0.1)

    # Low-confidence guard: if below the floor AND we can't fall back, null out docs_url
    if docs_url and confidence < _MIN_ACCEPTABLE_CONFIDENCE and not allow_fallback:
        docs_url = None

    source_signals = {
        "registry_source": registry_hint.source,
        "registry_exists": registry_hint.exists,
        "search_hits": len(hits),
        "llm_rerank": rerank is not None,
        "llm_error": rerank_error,
        "topic_reason": topic.reason or None,
        "root_liveness": liveness.status if liveness else None,
        "spot_check_valid": (
            f"{spot.valid_count}/{spot.total_count}" if spot else None
        ),
        "spot_downgraded": bool(spot.downgrade_applied) if spot else False,
        "github_discover": discovery_meta.get("github_discover"),
        "github_has_pages": discovery_meta.get("has_pages"),
        "github_archived": discovery_meta.get("archived"),
    }

    resolved = ResolvedDocs(
        canonical_name = resolved_name,
        docs_url = docs_url,
        repo_url = repo_url,
        registry_url = registry_url,
        version = (version or "latest"),
        tier = tier,
        tier_evidence = tier_evidence,
        confidence = confidence,
        fallback_candidates = fallback_candidates,
        source_signals = source_signals,
    )

    # Cache — TTL depends on confidence (see constants at top)
    try:
        await _cache_set(redis_aio, key, resolved)
    except Exception as e:
        logger.warning(f"[resolver] cache write failed for {canonical!r}: {e}")

    logger.info(
        f"[resolver] {canonical!r} → tier={tier} confidence={confidence:.2f} "
        f"docs_url={docs_url!r}"
    )
    return resolved


# =============================================================================
# Public entry point — resolve(request) → list[ResolvedDocs]
# =============================================================================
async def resolve(
    request: ResolveRequest,
    llm: ChatOpenAI,
    search_chain: SearchFallbackChain,
    redis_aio) -> list[ResolvedDocs]:
    """
    Main entry point. Runs the crossover decomposer, then fans out per topic.
    Returns length-1 list for single frameworks, length-N for crossover.

    Args:
        request: validated ResolveRequest.
        llm: LangChain chat model supporting function_calling. Same instance
             used for decompose() and the rerank pass — cheap classifier
             (Groq 8B) is the right fit; burns ~2 calls per topic.
        search_chain: shared multi-provider fallback chain from
             services.search_chain. Cascades Exa → Tavily → Jina on
             rate-limit / quota / 5xx. Per-provider cooldown state is
             shared across all topics in this call.
        redis_aio: app.state.redis_aio (async Redis client). Used for the
                   confidence-based cache.
    """
    # Crossover decomposition — 1 LLM call, ~500ms
    decomposition = await decompose(
        framework = request.framework,
        aliases = request.aliases,
        llm = llm,
    )

    # Fan out Stages A-D per topic. Each topic independent → asyncio.gather
    # is the natural primitive. Crossover of N topics ≈ one topic's latency.
    tasks = [
        _resolve_topic(
            topic = t,
            version = request.version,
            aliases = request.aliases,
            llm = llm,
            search_chain = search_chain,
            allow_fallback = request.allow_fallback,
            redis_aio = redis_aio,
            force_refresh = request.force_refresh,
        )
        for t in decomposition.topics
    ]
    results = await asyncio.gather(*tasks, return_exceptions = True)

    # Normalize exceptions into structured low-confidence ResolvedDocs so the
    # caller always receives a uniform shape. Individual topic failure in a
    # crossover request must not kill the other topics.
    out: list[ResolvedDocs] = []
    for topic, res in zip(decomposition.topics, results):
        if isinstance(res, Exception):
            logger.warning(
                f"[resolver] topic {topic.canonical_name!r} errored: {res}"
            )
            placeholder = TierProbe(
                url = "(none)",
                result = "ERROR",
                reason = f"resolver error: {type(res).__name__}",
                bytes_read = 0,
            )
            out.append(ResolvedDocs(
                canonical_name = topic.canonical_name,
                docs_url = None,
                tier = 4,
                tier_evidence = TierEvidence(
                    llms_full_txt = placeholder,
                    llms_txt = placeholder,
                    sitemap_xml = placeholder,
                ),
                confidence = 0.0,
                fallback_candidates = [],
                source_signals = {"error": str(res)[:300]},
            ))
        else:
            out.append(res)
    return out


# =============================================================================
# Coalescence — group topics that share the same ingestion source
# =============================================================================
# When a crossover like "DeepAgents + LangChain + LangGraph" produces N
# ResolvedDocs that all point at the same Tier 1 llms-full.txt (or Tier 2/3/4
# source on the same host), creating N separate studies downloads the same
# corpus N times and synthesizes N redundant plans. `coalesce_studies` groups
# such topics into one ResolvedStudy so the ingester + distiller run ONCE.
#
# The coalescing key must be strict enough to avoid false merges — two topics
# with the same tier but DIFFERENT sources (different llms-full.txt files, or
# different hosts) must stay separate. Key design:
#
#   Tier 1  → ("t1", llms_full_txt.url)
#   Tier 2  → ("t2", host, llms_txt.url)
#   Tier 3  → ("t3", host, sitemap_xml.url)
#   Tier 4  → ("t4", host)
#   Tier-GH → None (each repo is its own source)
#   unresolved → None (docs_url is None — nothing to merge)
#
# None means "this topic is in a group by itself." We assign a unique synthetic
# key so it doesn't collide with any other solo member.
def _coalesce_key(r: ResolvedDocs) -> Optional[tuple]:
    """
    Return the coalescing key for a resolved topic. None = keep solo.

    Strictness rationale: we only coalesce when evidence DIRECTLY confirms
    the shared source. A tier probe that returned SPA_FAKE / MISSING / ERROR
    on that file is not a valid coalescing anchor even if tier==N.
    """
    if not r.docs_url:
        return None
    if (r.source_signals or {}).get("github_discover") == "readme_only":
        return None
    ev = r.tier_evidence
    host = (urlparse(r.docs_url).netloc or "").lower()
    if r.tier == 1 and ev.llms_full_txt.result == "VALID":
        return ("t1", ev.llms_full_txt.url)
    if r.tier == 2 and ev.llms_txt.result == "VALID":
        return ("t2", host, ev.llms_txt.url)
    if r.tier == 3 and ev.sitemap_xml.result == "VALID":
        return ("t3", host, ev.sitemap_xml.url)
    if r.tier == 4:
        return ("t4", host)
    return None


def coalesce_studies(resolved: list[ResolvedDocs]) -> list[ResolvedStudy]:
    """
    Group `resolved` topics sharing the same ingestion source into a
    smaller list of `ResolvedStudy`. Order-preserving: each group keeps
    the position of its first member in the output.

    Length-1 groups (solo topics) are still returned as ResolvedStudy so
    downstream code can iterate uniformly. Callers that only care about
    coalescing can filter on `s.coalesced_from >= 2`.
    """
    groups: "OrderedDict[tuple, list[ResolvedDocs]]" = OrderedDict()
    solo_counter = 0
    for r in resolved:
        key = _coalesce_key(r)
        if key is None:
            # Synthetic unique key so this member stays in its own group
            # without ever colliding with real coalescing keys.
            key = ("solo", solo_counter)
            solo_counter += 1
        groups.setdefault(key, []).append(r)

    studies: list[ResolvedStudy] = []
    for _, members in groups.items():
        primary = members[0]
        host: Optional[str] = None
        if primary.docs_url:
            host = (urlparse(primary.docs_url).netloc or "").lower() or None
        shared_host = host if len(members) >= 2 else None

        # Union of repo URLs — distinct, order-preserving.
        repo_seen: set[str] = set()
        repos: list[str] = []
        for m in members:
            if m.repo_url and m.repo_url not in repo_seen:
                repo_seen.add(m.repo_url)
                repos.append(m.repo_url)

        docs_urls = [m.docs_url for m in members if m.docs_url]

        studies.append(ResolvedStudy(
            canonical_names = [m.canonical_name for m in members],
            docs_urls = docs_urls,
            primary_docs_url = primary.docs_url,
            shared_host = shared_host,
            tier = primary.tier,
            tier_evidence = primary.tier_evidence,
            repo_urls = repos,
            version = primary.version,
            confidence = min(m.confidence for m in members),
            coalesced_from = len(members),
            members = members,
        ))

    if any(s.coalesced_from >= 2 for s in studies):
        merged_count = sum(s.coalesced_from for s in studies if s.coalesced_from >= 2)
        merged_groups = sum(1 for s in studies if s.coalesced_from >= 2)
        logger.info(
            f"[resolver] coalesced {merged_count} topics into {merged_groups} "
            f"unified studies (total_studies={len(studies)})"
        )
    return studies
