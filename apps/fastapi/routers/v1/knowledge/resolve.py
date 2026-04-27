"""
POST /api/v1/knowledge/resolve

Deterministic resolver with LLM-cascade fallback for prose decomposition.

Pipeline per candidate framework name:
  Layer 0:  catalog (sources.yaml)                       instant, perfect
  Layer 0b: llms.txt directory mirror                    instant if mirrored
  Layer 1:  ecosyste.ms                                  free, ~85% of libraries
  Layer 2:  search-API rotator (ONE provider per call)   free-tier conservation
  Convergence: RRF + D0 hard gates                       Elasticsearch-grade fusion

Input filter:
  - query_splitter handles `+`, `,`, `;`, ` and `, `&`
  - LLM cascade fallback (existing app.state.llm) decomposes multi-word
    candidates that look conversational/acronym-shaped (e.g., "LGTM stack"
    → ["Loki", "Grafana", "Tempo", "Mimir"]). Single LLM call per
    multi-word candidate, structured JSON output.

CRITICAL: search APIs rotated SINGLE-PROVIDER-AT-A-TIME (no fan-out)
to economize each provider's ~1000 reqs/mo free quota.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

import httpx
from fastapi import APIRouter, HTTPException, Request
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from services.resolver import (
    CandidateURL,
    CatalogEntry,
    DepsDevHit,
    LlmsTxtEntry,
    LlmsTxtProbeResult,
    RootLiveness,
    SearchResult,
    SearchRotator,
    fuse_and_pick,
    fuzzy_lookup_catalog,
    lookup_by_name,
    lookup_catalog,
    lookup_depsdev,
    lookup_llmstxt,
    pick_canonical_url,
    probe_llmstxt,
    probe_root_liveness,
    split_query,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# Module-level rotator — singleton; loads quota counters from Redis on first use.
# (Real wiring of redis_aio happens in app.state lifespan; for now process-local
# counter is fine until we plumb it through.)
_rotator = SearchRotator(redis_aio=None)


class ResolveRequest(BaseModel):
    query: str = Field(
        description=(
            "Free-text query — single framework ('FastAPI') or multi-framework "
            "crossover ('LangChain + LangGraph + DeepAgents'). Splits on +, ',', "
            "';', ' and ', ' & '."
        ),
    )


def _liveness_dict(rl: RootLiveness | None) -> dict[str, Any] | None:
    if rl is None:
        return None
    return {
        "status": rl.status,
        "reason": rl.reason,
        "docs_signals": rl.docs_signals,
        "final_url": rl.final_url,
    }


def _catalog_result(name: str, cat: CatalogEntry,
                    rl: RootLiveness | None) -> dict[str, Any]:
    return {
        "framework": name,
        "source": "catalog",
        "docs_url": cat.docs_url,
        "repo_url": cat.repo_url,
        "rrf_score": None,
        "contributors": ["catalog"],
        "liveness": _liveness_dict(rl),
        "catalog_extras": {
            "name": cat.name,
            "aliases": cat.aliases,
            "llms_full_txt": cat.llms_full_txt,
            "llms_txt": cat.llms_txt,
            "sitemap_xml": cat.sitemap_xml,
            "notes": cat.notes,
        },
    }


def _llmstxt_result(name: str, entry: LlmsTxtEntry,
                    rl: RootLiveness | None) -> dict[str, Any]:
    return {
        "framework": name,
        "source": "llmstxt-hub",
        "docs_url": entry.docs_url,
        "repo_url": None,
        "rrf_score": None,
        "contributors": ["llmstxt-hub"],
        "liveness": _liveness_dict(rl),
        "llmstxt_extras": {
            "llms_url": entry.llms_url,
            "llms_full_url": entry.llms_full_url,
            "category": entry.category,
        },
    }


# NOTE: previously had a `_is_strong_canonical` heuristic that skipped Layer 2
# search when ecosyste.ms / deps.dev returned a "strong" signal (DOCUMENTATION
# field or docs.* host). This was a premature optimization — it routinely
# returned a registry's SDK page (e.g., docker-py.readthedocs.io) instead of
# the platform docs (docs.docker.com) because the package's documentation_url
# IS docker-py. Search is the strongest discovery signal for vendor portals
# and the free-tier quota across 4 providers (~3800/month) far exceeds use.
# Layers 0 (catalog) and 0b (llmstxt-hub) still short-circuit because those
# ARE publisher-asserted; everything else now goes through full convergence.


def _build_search_query(name: str) -> str:
    """
    Best 2026 query: `{name} documentation` (no quotes, no 'official').
    Empirical: avoiding 'official' reduces marketing-page bias; unquoted lets
    LLM-ranked search engines (Exa/Tavily) match docs intent naturally.
    """
    return f"{name} documentation"


async def _resolve_one(
    candidate: str, client: httpx.AsyncClient,
) -> dict[str, Any]:
    """
    Per-candidate pipeline. Returns the resolver response dict.

    Layer ordering keeps paid resources (search APIs) LAST and conditional —
    fired only when free layers (catalog, llmstxt, ecosyste.ms) don't yield
    a high-confidence match.
    """

    # --------- Tier 0: catalog (instant, perfect) ---------
    cat = lookup_catalog(candidate)
    if cat is not None and cat.docs_url:
        rl = await probe_root_liveness(cat.docs_url, client=client)
        return _catalog_result(candidate, cat, rl)

    # --------- Tier 0b: llms.txt directory mirror ---------
    lt = lookup_llmstxt(candidate)
    if lt is not None and lt.docs_url:
        rl = await probe_root_liveness(lt.docs_url, client=client)
        return _llmstxt_result(candidate, lt, rl)

    # --------- Tier 1 + 1.5: ecosyste.ms + deps.dev (parallel) ---------
    # Both call cross-registry metadata — pair for redundancy:
    #   - ecosyste.ms: 100+ ecosystems incl. long-tail (Hex, CRAN, Conda, ...)
    #     5000 req/hr/IP — but intermittently 500s on popular names.
    #   - deps.dev: 7 mainstream ecosystems (PYPI/NPM/CARGO/GO/MAVEN/RUBYGEMS/NUGET)
    #     no documented rate limit, more reliable on common names.
    # RRF fusion dedupes when both agree; tiebreakers prefer publisher-asserted.
    eco_hits, depsdev_hit = await asyncio.gather(
        lookup_by_name(candidate, client=client),
        lookup_depsdev(candidate, client=client),
        return_exceptions=False,
    )
    eco_ranked = pick_canonical_url(eco_hits, candidate) if eco_hits else None

    # Collect candidate URLs for convergence.
    candidates: list[CandidateURL] = []
    if eco_ranked is not None:
        candidates.append(CandidateURL(
            url=eco_ranked.url,
            source="ecosystems",
            rank=1,
            notes=f"{eco_ranked.field} from {eco_ranked.hit.ecosystem}",
            field=eco_ranked.field,
        ))
    if depsdev_hit is not None:
        if depsdev_hit.docs_url:
            candidates.append(CandidateURL(
                url=depsdev_hit.docs_url,
                source="depsdev",
                rank=1,
                notes=f"{depsdev_hit.docs_url_label} from {depsdev_hit.ecosystem}",
                field=depsdev_hit.docs_url_label,
            ))
        # If docs_url == homepage we already covered it; otherwise add as rank 2.
        if (
            depsdev_hit.homepage
            and depsdev_hit.homepage != depsdev_hit.docs_url
        ):
            candidates.append(CandidateURL(
                url=depsdev_hit.homepage,
                source="depsdev",
                rank=2,
                notes=f"HOMEPAGE from {depsdev_hit.ecosystem}",
                field="HOMEPAGE",
            ))

    # --------- Tier 2: search-API rotator (ONE provider, ALWAYS runs) ---------
    # Search is the strongest signal for vendor-portal queries (Docker → docs.docker.com,
    # MongoDB → mongodb.com/docs) where deps.dev / ecosyste.ms point at SDKs/drivers.
    # Cost is one rotator call per candidate (rotator picks the provider with best
    # quota+EWMA). Catalog/llmstxt-hub already short-circuited above.
    search_result = await _rotator.search(
        _build_search_query(candidate), client=client,
    )
    if search_result is not None and search_result.url:
        candidates.append(CandidateURL(
            url=search_result.url,
            source=f"search:{search_result.provider}",
            rank=1,
            notes=search_result.title[:80],
        ))

    # --------- Convergence (RRF + D0) ---------
    if not candidates:
        return {
            "framework": candidate,
            "source": "unresolved",
            "docs_url": None,
            "repo_url": None,
            "rrf_score": None,
            "contributors": [],
            "liveness": None,
            "reason": (
                "no catalog / llmstxt / ecosyste.ms / search-api candidates "
                "(framework not in any tracked source)"
            ),
        }

    # --------- D0 liveness + Layer 4.5 direct llms.txt probe (parallel) ---------
    # For each unique candidate URL: run D0 root-liveness probe AND a direct
    # {url}/llms.txt + /llms-full.txt probe. The latter validates content
    # (HTTP 200, not HTML, ≥ size, link/heading patterns). Any URL that
    # passes the llms.txt probe gets re-injected as a top-rank candidate
    # with source 'llmstxt-probe' before final RRF.
    from services.resolver.convergence import _canonicalize  # local import to avoid cycle
    unique_urls = list({_canonicalize(c.url) for c in candidates if c.url})

    d0_coros = [probe_root_liveness(u, client=client) for u in unique_urls]
    probe_coros = []
    for u in unique_urls:
        # Two probes per URL: llms.txt + llms-full.txt (concatenated below).
        probe_coros.append(probe_llmstxt(u, "llms_txt", client=client))
        probe_coros.append(probe_llmstxt(u, "llms_full_txt", client=client))

    all_results = await asyncio.gather(
        *d0_coros, *probe_coros, return_exceptions=False,
    )
    d0_probes = all_results[: len(unique_urls)]
    probe_pairs = all_results[len(unique_urls):]

    d0_results = {
        u: {"status": p.status, "docs_signals": len(p.docs_signals),
            "final_url": p.final_url}
        for u, p in zip(unique_urls, d0_probes)
    }

    # Re-inject any URL that hosted a valid llms.txt or llms-full.txt
    # as a rank-1 contributor under the llmstxt-probe source. Source-priority
    # tiebreaker pushes those URLs above search-API hits at fusion time.
    llmstxt_evidence: dict[str, dict] = {}
    for i, u in enumerate(unique_urls):
        ll = probe_pairs[2 * i]
        lf = probe_pairs[2 * i + 1]
        if ll.found or lf.found:
            llmstxt_evidence[u] = {
                "llms_txt_url": ll.url if ll.found else None,
                "llms_full_txt_url": lf.url if lf.found else None,
            }
            candidates.append(CandidateURL(
                url=u,
                source="llmstxt-probe",
                rank=1,
                notes=("llms-full+llms" if (ll.found and lf.found)
                       else ("llms-full" if lf.found else "llms")),
                field="DOCUMENTATION",  # publisher-asserted by definition
            ))

    fused = fuse_and_pick(candidates, framework=candidate, d0_results=d0_results)
    repo_url = (
        eco_ranked.hit.repository_url if eco_ranked is not None
        else (depsdev_hit.repository_url if depsdev_hit is not None else None)
    )

    if fused is None:
        return {
            "framework": candidate,
            "source": "unresolved",
            "docs_url": None,
            "repo_url": repo_url,
            "rrf_score": None,
            "contributors": [c.source for c in candidates],
            "liveness": None,
            "reason": "all candidates rejected by D0 gates (DEAD/PARKED/no-docs-signals)",
        }

    # Mark explicitly low-confidence when RRF score is below threshold
    # (fused.rejection_reason populated by convergence). Caller must treat
    # source="low_confidence" as "do not auto-trust".
    src_label = "low_confidence" if fused.rejection_reason else "discovered"

    chosen_evidence = llmstxt_evidence.get(fused.canonical_url) or {}

    return {
        "framework": candidate,
        "source": src_label,
        "docs_url": fused.canonical_url,
        "repo_url": repo_url,
        "rrf_score": round(fused.rrf_score, 4),
        "contributors": [c.source for c in fused.contributors],
        "liveness": {
            "status": fused.liveness_status,
            "docs_signals": fused.docs_signals,
            "final_url": fused.final_url,
        },
        "low_confidence_reason": fused.rejection_reason or None,
        "ecosystems_extras": {
            "registries_seen": sorted({h.ecosystem for h in eco_hits}) if eco_hits else [],
            "top_package": eco_ranked.hit.name if eco_ranked else None,
        } if eco_hits else None,
        "depsdev_extras": {
            "ecosystem": depsdev_hit.ecosystem,
            "package": depsdev_hit.package_name,
            "version": depsdev_hit.version,
            "docs_url_label": depsdev_hit.docs_url_label,
        } if depsdev_hit else None,
        "llmstxt_probe": {
            "llms_txt_url": chosen_evidence.get("llms_txt_url"),
            "llms_full_txt_url": chosen_evidence.get("llms_full_txt_url"),
        } if chosen_evidence else None,
        "search_extras": {
            "provider": search_result.provider,
            "title": search_result.title,
        } if search_result else None,
    }


class _DecomposedEntities(BaseModel):
    """Structured output schema for LLM decomposition."""
    frameworks: list[str] = Field(
        description=(
            "Canonical framework / library / SDK / programming-language / "
            "developer-tool names extracted from the query. Decompose stacks "
            "and acronyms into their components (LGTM stack → "
            "[Loki, Grafana, Tempo, Mimir]). Empty list if no tech entities."
        ),
    )


_DECOMPOSE_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You extract the canonical names of code frameworks, libraries, SDKs, "
        "programming languages, infrastructure tools, and developer products "
        "from short user queries. Output rules:\n"
        "  1. Decompose ACRONYMS or STACK-NAMES into their components: "
        "'LGTM stack' → ['Loki', 'Grafana', 'Tempo', 'Mimir']; "
        "'MEAN stack' → ['MongoDB', 'Express', 'Angular', 'Node.js']; "
        "'JAMstack' → ['JavaScript', 'APIs', 'Markup'].\n"
        "  2. Use canonical names as published by the project (e.g., 'Pydantic' "
        "not 'pydantic'; 'FastAPI' not 'Fast API'; 'Apache Kafka' not 'Kafka'\n"
        "  3. Do NOT include generic categories ('cloud', 'database', 'IDE') "
        "or method-of-use words ('deploy', 'configure', 'install').\n"
        "  4. Return EMPTY list if the query is non-technical "
        "('how to bake a cake') or contains zero specific tech entities.\n"
        "  5. Maximum 8 entities per query."
    ),
    (
        "human",
        "Query: {query}\n\nExtract canonical framework/library/SDK/tool names.",
    ),
])


async def _llm_decompose(query: str, llm) -> list[str]:
    """
    Single LLM-cascade call to decompose a multi-word / acronym-shaped query
    into canonical framework names. Uses the existing app.state.llm router
    (LiteLLM cascade — free-tier providers in priority order).

    Returns empty list on any LLM failure (graceful degradation).
    """
    if llm is None or not query or not query.strip():
        return []
    try:
        chain = _DECOMPOSE_PROMPT | llm.with_structured_output(
            _DecomposedEntities, method="function_calling",
        )
        result = await asyncio.wait_for(
            chain.ainvoke({"query": query.strip()}), timeout=20.0,
        )
        if result is None or not result.frameworks:
            return []
        # Dedupe case-insensitively, preserve order, drop empties.
        seen: set[str] = set()
        out: list[str] = []
        for name in result.frameworks:
            n = (name or "").strip()
            if not n:
                continue
            k = n.lower()
            if k not in seen:
                seen.add(k)
                out.append(n)
        return out[:8]   # hard cap
    except (asyncio.TimeoutError, Exception) as e:
        logger.warning(
            f"[resolver._llm_decompose] LLM decomposition failed for "
            f"{query[:60]!r}: {type(e).__name__}: {e}"
        )
        return []


async def _input_filter(raw: str, llm=None) -> tuple[list[str], str]:
    """
    Frontend filter — converts arbitrary user query to a list of canonical
    framework names. Layered strategy:

      1. query_splitter handles structural separators (+, comma, ;, and, &)
      2. For each token:
         a. If single word → use as-is (catalog/ecosyste.ms will resolve)
         b. If multi-word → fire LLM decomposition (handles acronyms, stacks,
            prose like "Deploy a module on Terraform")
      3. Per-token catalog fuzzy match for typo tolerance on bare names
      4. Dedupe case-insensitively, preserve insertion order

    Returns (candidates, mode_label_for_response).
    """
    raw = (raw or "").strip()
    if not raw:
        return [], "empty"

    tokens = split_query(raw)
    if not tokens:
        return [], "empty"

    # Per-token expansion: single-word tokens pass through (catalog/ecosyste.ms
    # will handle); multi-word tokens go to LLM decomposition.
    expanded: list[str] = []
    used_llm = False
    rejected_by_llm = False
    for t in tokens:
        if len(t.split()) == 1:
            expanded.append(t)
        else:
            # Multi-word token — try LLM cascade decomposition.
            if llm is not None:
                decomposed = await _llm_decompose(t, llm)
                if decomposed:
                    expanded.extend(decomposed)
                    used_llm = True
                else:
                    # LLM ran AND returned [] → strong signal that this
                    # token has NO tech entities (e.g., "how to bake a cake").
                    # REJECT it — don't fall back to raw, otherwise
                    # ecosyste.ms's last_token variant matches garbage
                    # like "cake" → hexdocs.pm/cake.
                    rejected_by_llm = True
                    used_llm = True
            else:
                # LLM unavailable (no app.state.llm) → trust the multi-word
                # token as-is; ecosyste.ms variant fallback may still recover.
                expanded.append(t)

    # Catalog fuzzy match per candidate (typo tolerance: "FastApi" → "FastAPI").
    fuzzied: list[str] = []
    for c in expanded:
        cat = fuzzy_lookup_catalog(c)
        fuzzied.append(cat.name if cat else c)

    # Dedupe case-insensitively, preserve order.
    seen: set[str] = set()
    out: list[str] = []
    for c in fuzzied:
        k = c.lower().strip()
        if k and k not in seen:
            seen.add(k)
            out.append(c)

    if not out:
        # If LLM was available AND rejected at least one multi-word token,
        # this is a confident "no tech here" — return rejection.
        return [], (
            "no-tech-identified-by-llm" if rejected_by_llm
            else "no-tech-identified"
        )

    mode = (
        "splitter+llm-decompose" if used_llm
        else "splitter+catalog-fuzzy"
    )
    return out, mode


def _dedupe_results(results: list[dict]) -> list[dict]:
    """
    Group results that resolved to the same canonical docs URL. When 2+
    framework names hit the same URL (e.g., LangChain + LangGraph + DeepAgents
    all → docs.langchain.com), collapse them into ONE entry with
    `frameworks: [...]` instead of duplicate per-name entries.
    """
    from services.resolver.convergence import _canonicalize

    by_url: dict[str, dict] = {}
    no_url: list[dict] = []
    for r in results:
        url = r.get("docs_url")
        if not url:
            no_url.append(r)
            continue
        canon = _canonicalize(url)
        if canon in by_url:
            existing = by_url[canon]
            existing["frameworks"].append(r["framework"])
            # Union contributors
            existing_contribs = set(existing.get("contributors") or [])
            new_contribs = set(r.get("contributors") or [])
            existing["contributors"] = sorted(existing_contribs | new_contribs)
        else:
            r["frameworks"] = [r["framework"]]
            by_url[canon] = r
    return list(by_url.values()) + no_url


@router.post("/resolve")
async def resolve(payload: ResolveRequest, request: Request):
    """
    Deterministic resolver with LLM-cascade decomposition for prose queries.

    Stages:
      1. _input_filter:
         - query_splitter (+, comma, ;, and, &)
         - LLM decomposition for multi-word tokens (LGTM stack → 4 names)
         - catalog fuzzy match for typo tolerance
      2. Each entity resolved through catalog → llmstxt → ecosyste.ms →
         search-rotator → RRF + D0.
      3. Dedupe by canonical docs URL (LangChain ecosystem collapses to one).
    """
    raw = (payload.query or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="query must be non-empty")

    # Use the existing app.state.llm cascade (LiteLLM router) for any
    # decomposition needed. None-safe: if app didn't initialize it, the
    # filter falls back to splitter-only behavior.
    llm = getattr(request.app.state, "llm", None)
    candidates, filter_mode = await _input_filter(raw, llm=llm)

    if not candidates:
        return {
            "input": raw,
            "is_crossover": False,
            "filter_mode": filter_mode,
            "results": [],
            "rejection_reason": (
                "no technical framework identified in query — resolver only "
                "handles code frameworks, libraries, SDKs, CLIs, and developer tools"
            ),
        }

    async with httpx.AsyncClient(
        headers={"User-Agent": "COELHONexus-resolver/1.0"},
        timeout=20.0,
        limits=httpx.Limits(max_connections=30),
    ) as client:
        raw_results = await asyncio.gather(*[
            _resolve_one(c, client) for c in candidates
        ])

    results = _dedupe_results(raw_results)

    return {
        "input": raw,
        "filter_mode": filter_mode,
        "is_crossover": len(candidates) > 1,
        "extracted_entities": candidates,
        "results": results,
        "search_quota": _rotator.status(),
    }
