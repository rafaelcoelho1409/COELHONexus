"""
Resolver — deterministic, LLM-free except for prose decomposition.

Pipeline (parallelized where free; serialized for paid):
  Layer 0:   catalog (sources.yaml)               instant, perfect
  Layer 0b:  llms.txt directory mirror            in-process, refreshed every 24h
  Layer 1:   ecosyste.ms /packages/lookup         free, ~85% of libraries
  Layer 1.5: deps.dev (Google) 2-call pattern     free, no rate limit, 7 ecosystems
  Layer 2:   search-API rotator (ONE provider)    free quota — economized
                                                    Exa → Tavily → Linkup → Jina
  Layer 4.5: direct {url}/llms.txt HEAD probe     content-validated
  Convergence: RRF (k=60) + D0 hard gates +       industry-standard fusion
               publisher-asserted tiebreakers     (catalog>llmstxt-hub>depsdev>...)
"""

from .catalog import CatalogEntry, fuzzy_lookup_catalog, load_catalog, lookup_catalog
from .convergence import CandidateURL, FusedCandidate, fuse_and_pick
from .depsdev import DepsDevHit, lookup_depsdev, normalize_ecosystem
from .ecosystems import (
    EcosystemsHit,
    RankedURL,
    lookup_by_name,
    lookup_by_repo,
    pick_canonical_url,
)
from .liveness import RootLiveness, probe_root_liveness
from .llmstxt import (
    LlmsTxtEntry,
    bootstrap as bootstrap_llmstxt,
    load_llmstxt,
    lookup_llmstxt,
    refresh_llmstxt,
    refresh_loop as llmstxt_refresh_loop,
)
from .llmstxt_probe import LlmsTxtProbeResult, probe_llmstxt
from .query_splitter import split_query
from .search_rotator import SearchResult, SearchRotator

__all__ = [
    "split_query",
    # catalog
    "load_catalog", "lookup_catalog", "fuzzy_lookup_catalog", "CatalogEntry",
    # llms.txt mirror (in-process; bootstrap + refresh in lifespan)
    "bootstrap_llmstxt", "refresh_llmstxt", "llmstxt_refresh_loop",
    "load_llmstxt", "lookup_llmstxt", "LlmsTxtEntry",
    # ecosyste.ms
    "lookup_by_name", "lookup_by_repo", "pick_canonical_url",
    "EcosystemsHit", "RankedURL",
    # deps.dev
    "lookup_depsdev", "normalize_ecosystem", "DepsDevHit",
    # llms.txt direct probe
    "probe_llmstxt", "LlmsTxtProbeResult",
    # search rotator
    "SearchRotator", "SearchResult",
    # liveness
    "probe_root_liveness", "RootLiveness",
    # convergence
    "fuse_and_pick", "CandidateURL", "FusedCandidate",
]
