"""
Knowledge Distiller — Resolver Schemas

Registry existence-only + SearXNG fan-out + LLM rerank + content-validated
tier classification. See docs/KNOWLEDGE-DISTILLER-RESOLVER-STRATEGY.md for
design rationale.

These models cover three I/O surfaces:

  1. Input  — ResolveRequest (single framework OR crossover string).
  2. LLM I/O — structured outputs for the decomposer + reranker passes.
  3. Output — ResolvedDocs (one per canonical topic).

Every `Field(description=...)` doubles as an LLM prompt via
`with_structured_output(Model)`, so keep them explicit, not ornamental.
"""
from typing import Annotated, Literal, Optional
from pydantic import BaseModel, Field, StringConstraints


NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace = True, min_length = 1)]


Tier = Literal[1, 2, 3, 4]
"""
Ingestion tier — routing decision for the crawler:
  1 — /llms-full.txt valid → fetch one file, ~seconds
  2 — /llms.txt valid       → fetch index + parallel .md links, ~1 min
  3 — /sitemap.xml valid    → enumerate URLs, filter, fetch, ~minutes
  4 — all missing           → full Playwright crawl, ~20 min
"""


ProbeResult = Literal["VALID", "SPA_FAKE", "MISSING", "ERROR"]
"""
Content-validation result for a probed file (llms-full.txt / llms.txt / sitemap.xml).
- VALID    → real content, shape matches expected format
- SPA_FAKE → HTTP 200 but body is SPA shell HTML (not a real file)
- MISSING  → 404 / 4xx / 5xx / redirected off
- ERROR    → network timeout / connection refused / other I/O failure
"""


RootLivenessStatus = Literal["LIVE", "EMPTY_SHELL", "PARKED", "DEAD", "ERROR"]
"""
Liveness verdict for the docs_url itself (Stage D0):
- LIVE        → reachable HTML page with docs-site signals (nav/headings/code/markdown mentions)
- EMPTY_SHELL → reachable but the body is effectively empty after tag stripping (dead SPA)
- PARKED      → reachable but the body advertises a domain-for-sale / parking page
- DEAD        → HTTP 4xx / 5xx, or redirects to an unrelated host
- ERROR       → network timeout / connection refused / other I/O failure
"""


SpotCheckStatus = Literal["VALID", "EMPTY", "MISSING", "ERROR"]
"""
Per-URL sample verdict (Stage D2). Distinguishes the two degradation modes
that an otherwise-VALID sitemap/llms.txt can hide:
- VALID   → sample URL returns real content
- EMPTY   → HTTP 200 but the page is effectively empty (SPA shell, placeholder)
- MISSING → 404 / other 4xx — the index references a non-existent page
- ERROR   → network failure on the sample
"""


# =============================================================================
# Input — POST /studies/resolve
# =============================================================================
class ResolveRequest(BaseModel):
    """
    Single framework OR crossover-study string.

    Examples:
        framework="FastAPI"
        framework="Grafana Alloy + LGTM + PromQL + LogQL + River"
        framework="DeepAgents + LangChain + LangGraph"

    A lightweight LLM decomposer runs first to classify single-vs-crossover.
    When crossover is detected, the pipeline fans out per canonical topic via
    asyncio.gather — each topic gets an independent ResolvedDocs.
    """
    framework: NonEmptyStr = Field(
        description = (
            "Framework name OR crossover request. Plain names ('FastAPI', "
            "'React') produce length-1 results; strings with '+' or commas "
            "trigger crossover decomposition."
        ),
    )
    version: Optional[NonEmptyStr] = Field(
        default = None,
        description = (
            "Optional version hint. Flows through as a STRING HINT only — it "
            "biases SearXNG queries and the LLM rerank prompt, but no code "
            "derives a docs URL from it. Registry publishers version their "
            "docs sites with incompatible conventions, so the LLM reads "
            "candidate URLs and picks the version-matching one."
        ),
    )
    aliases: list[NonEmptyStr] = Field(
        default_factory = list,
        description = "Optional synonyms the LLM should treat as the same topic.",
    )
    allow_fallback: bool = Field(
        default = True,
        description = (
            "If True (default), low-confidence or missing-tier results return "
            "partial ResolvedDocs with fallback_candidates populated instead "
            "of raising. Set False to fail loud when confidence < 0.3."
        ),
    )
    force_refresh: bool = Field(
        default = False,
        description = "Bypass the Redis cache and re-run the full pipeline.",
    )


# =============================================================================
# Registry — Stage A (existence check only)
# =============================================================================
class RegistryHint(BaseModel):
    """
    Minimal registry lookup result. Registry is existence-only — it does NOT
    compute docs URLs. Version flows through as a string hint.
    """
    exists: bool = Field(
        description = "True if the package exists in at least one registry (PyPI / npm / deps.dev / crates.io).",
    )
    homepage: Optional[str] = Field(
        default = None,
        description = "Canonical homepage/docs URL as declared by the publisher.",
    )
    repo: Optional[str] = Field(
        default = None,
        description = "Canonical source repository URL (github.com, gitlab.com, etc.).",
    )
    latest_version: Optional[str] = Field(
        default = None,
        description = "Registry's current 'latest' marker, if any.",
    )
    all_versions: list[str] = Field(
        default_factory = list,
        description = "All versions the registry knows about, newest-first, capped to ~30.",
    )
    source: Optional[str] = Field(
        default = None,
        description = "Which registry answered: 'pypi', 'npm', 'crates.io', 'rubygems', 'go', or null when not found anywhere.",
    )


# =============================================================================
# SearXNG — Stage B (web-search candidates)
# =============================================================================
class SearxngHit(BaseModel):
    """One SearXNG result, normalized across engines."""
    url: str = Field(description = "Result URL, already validated to start with http(s)://.")
    title: str = Field(description = "Page title as returned by the engine.")
    snippet: str = Field(default = "", description = "Page snippet/excerpt; may be empty.")
    engine: str = Field(default = "", description = "Which search engine produced this hit.")


# =============================================================================
# LLM Rerank — Stage C output
# =============================================================================
class LLMRerankOutput(BaseModel):
    """
    Strict-schema output from the LLM rerank pass (Stage C).

    The LLM receives: framework name + aliases + version hint + registry
    homepage/repo + SearXNG candidates. It returns the single best canonical
    docs_url plus rejected candidates (so the caller can surface fallbacks
    on low confidence).
    """
    docs_url: str = Field(
        description = (
            "Canonical documentation root URL. MUST come from the CANDIDATES "
            "list — do NOT invent URLs. Prefer official publisher sites "
            "(vendor domains, org GitHub Pages). Prefer 'docs.*' subdomains "
            "or URLs ending in /docs. Reject PyPI/npm package pages, Reddit, "
            "HackerNews, StackOverflow, Medium, blog posts, forks with low "
            "stars. If framework has a dedicated docs subdomain, prefer that "
            "over a /docs folder URL."
        ),
    )
    repo_url: Optional[str] = Field(
        default = None,
        description = "Canonical source repo URL (github.com/org/repo) if clearly identifiable; else null.",
    )
    registry_url: Optional[str] = Field(
        default = None,
        description = "Registry page (PyPI/npm/crates.io) if relevant; else null. Use for reference, NOT for docs.",
    )
    canonical_name: str = Field(
        description = "Normalized framework name, e.g. 'FastAPI', 'LangChain', 'Apache Airflow'.",
    )
    confidence: float = Field(
        ge = 0.0,
        le = 1.0,
        description = (
            "Self-reported confidence. 0.9+ = one obvious winner; 0.7-0.9 = "
            "likely correct with one plausible alternative; 0.4-0.7 = "
            "ambiguous (multiple plausible candidates); <0.4 = guess."
        ),
    )
    rejected: list[str] = Field(
        default_factory = list,
        description = (
            "Up to 5 rejected candidate URLs in 'url:reason' format. Caller "
            "uses these as fallback_candidates when confidence is low."
        ),
    )


# =============================================================================
# Crossover decomposer — optional pre-pass LLM call
# =============================================================================
class DecompositionTopic(BaseModel):
    """One canonical topic extracted from a crossover request."""
    topic: str = Field(description = "Raw topic as mentioned in the input. Example: 'LogQL'.")
    canonical_name: str = Field(
        description = (
            "Canonicalized framework name the resolver will use. Normalize "
            "query-language aliases: 'LogQL' → 'Loki', 'PromQL' → 'Prometheus', "
            "'River' / 'River DSL' → 'Grafana Alloy', 'PySpark' → 'Apache Spark'."
        ),
    )
    reason: str = Field(
        default = "",
        description = "One-line justification for any non-obvious canonicalization.",
    )


class DecompositionResult(BaseModel):
    """Output of the crossover decomposer. Single frameworks → topics length 1."""
    is_crossover: bool = Field(
        description = "True when the input references ≥2 distinct technologies. Otherwise False.",
    )
    topics: list[DecompositionTopic] = Field(
        min_length = 1,
        max_length = 10,
        description = (
            "Canonical topics to resolve independently. For single frameworks, "
            "length 1. For crossover requests, one entry per distinct technology; "
            "canonicalize aliases so LogQL + Loki don't both appear."
        ),
    )


# =============================================================================
# Tier probe — Stage D evidence (content-validated)
# =============================================================================
class TierProbe(BaseModel):
    """
    Per-file probe result. Evidence kept for observability — surfaced as
    `tier_evidence` on the final ResolvedDocs.
    """
    url: str = Field(description = "URL probed (e.g., docs_url + '/llms-full.txt').")
    result: ProbeResult = Field(description = "Content-validation verdict.")
    reason: str = Field(description = "One-line reason — size, HTTP status, SPA shell detection, etc.")
    bytes_read: int = Field(default = 0, description = "Bytes of body inspected during validation.")


class RootLivenessProbe(BaseModel):
    """
    Stage D0 — did the resolved `docs_url` itself actually return something
    usable? Catches three failure modes that slip past file-level probing:
      - parked domains (publisher sold the domain)
      - dead SPA shells (routes removed, server still serves the app shell)
      - whole-site outages (all files miss together — ERROR cascade)
    Without D0 the resolver can return a URL that the crawler would then
    hit fruitlessly for 20+ minutes before concluding there's nothing there.
    """
    url: str = Field(description = "URL probed — usually the resolved docs_url.")
    status: RootLivenessStatus = Field(description = "Liveness verdict.")
    http_status: int = Field(default = 0, description = "HTTP status code, or negative for network errors.")
    reason: str = Field(description = "Short explanation — what was seen (body length, signals, title).")
    bytes_read: int = Field(default = 0, description = "Bytes of body inspected.")
    docs_signals: list[str] = Field(
        default_factory = list,
        description = "Docs-site markers detected (e.g., 'nav', 'h1', 'code', 'sidebar', 'toc').",
    )
    final_url: Optional[str] = Field(
        default = None,
        description = "URL after redirects. Useful when publishers redirect to a new canonical root.",
    )


class SpotCheckItem(BaseModel):
    """One sampled URL within a Stage D2 spot-check."""
    url: str = Field(description = "The sampled URL fetched.")
    status: SpotCheckStatus = Field(description = "Per-URL verdict.")
    http_status: int = Field(default = 0, description = "HTTP status code, or negative on network error.")
    reason: str = Field(description = "Short explanation of the verdict.")
    bytes_read: int = Field(default = 0, description = "Bytes of body inspected.")


class SpotCheckResult(BaseModel):
    """
    Stage D2 aggregate — a sample of 2-3 URLs drawn from the VALID index
    (sitemap.xml <loc> entries, or llms.txt .md links). Confirms the index
    isn't pointing at stale / 404 / empty-shell content.
    """
    source: Literal["sitemap", "llms_txt"] = Field(
        description = "Which index we sampled URLs from.",
    )
    samples: list[SpotCheckItem] = Field(
        default_factory = list,
        description = "Per-URL verdicts for each sampled URL (typically 3).",
    )
    valid_count: int = Field(default = 0, description = "How many samples came back VALID.")
    total_count: int = Field(default = 0, description = "Samples attempted (valid + empty + missing + error).")
    downgrade_applied: bool = Field(
        default = False,
        description = "True when majority of samples failed — resolver downgrades the tier accordingly.",
    )


class TierEvidence(BaseModel):
    """All probes + final tier assignment."""
    llms_full_txt: TierProbe
    llms_txt: TierProbe
    sitemap_xml: TierProbe
    root_liveness: Optional[RootLivenessProbe] = Field(
        default = None,
        description = "Stage D0 — docs_url itself validated (parked / dead SPA detection).",
    )
    spot_check: Optional[SpotCheckResult] = Field(
        default = None,
        description = "Stage D2 — sampled URLs from the winning index (only populated for tier 2/3).",
    )


# =============================================================================
# Output — ResolvedDocs (per canonical topic)
# =============================================================================
class ResolvedDocs(BaseModel):
    """
    Final resolver output. Length-1 list for single frameworks, length-N for
    crossover requests. Crawler consumes this directly — tier tells it which
    ingestion strategy to run.
    """
    canonical_name: str = Field(
        description = "Canonical framework name (LLM-normalized).",
    )
    docs_url: Optional[str] = Field(
        default = None,
        description = "Canonical docs root. Null when confidence <0.3 AND allow_fallback=True.",
    )
    repo_url: Optional[str] = Field(
        default = None,
        description = "Canonical source repo URL.",
    )
    registry_url: Optional[str] = Field(
        default = None,
        description = "Registry page (PyPI/npm/crates.io) if relevant — for reference only, NOT a docs source.",
    )
    version: str = Field(
        default = "latest",
        description = "Version hint echoed back ('latest' when none requested).",
    )
    tier: Tier = Field(
        description = (
            "Ingestion tier — routing decision for the crawler. 1 = fastest "
            "(llms-full.txt), 4 = slowest (full Playwright crawl)."
        ),
    )
    tier_evidence: TierEvidence = Field(
        description = "Per-file probe evidence supporting the tier classification.",
    )
    confidence: float = Field(
        ge = 0.0,
        le = 1.0,
        description = "Composite confidence (LLM self-report, optionally penalized on tier=4).",
    )
    fallback_candidates: list[str] = Field(
        default_factory = list,
        description = "Rejected LLM candidates — surfaced on low confidence so the user can pick manually.",
    )
    source_signals: dict = Field(
        default_factory = dict,
        description = (
            "Provenance payload — which registry answered, how many SearXNG "
            "hits, which model produced the rerank. Opaque to consumers but "
            "invaluable for debugging."
        ),
    )
