"""
Online benchmark fetcher + canonicalization + warm-start composite scorer.

DESIGN (2026-05-13, REV 2): Replaces six fragile fetchers (HF Open LLM v2,
MTEB, SWE-Bench, etc.) — none of which had real coverage of the frontier
free-tier models we actually serve — with three sources that empirically DO
cover our pool:

  Source                          Pool coverage   Format          Auth
  ─────────────────────────────   ─────────────   ─────────────   ─────
  OpenLM.ai Chatbot Arena+        ~95%            HTML (BS4)      none
  oolong-tea code.json (GitHub)   100% (coding)   JSON 2-step     none
  OpenEvals/leaderboard-data (HF) ~50% (open)     JSON direct     none

All three confirmed live 2026-05-13. See memory:
reference_llm_benchmark_sources.md for the full source map and the 11+
ruled-out sources.

Metrics surfaced after merge:
  lmarena         Chatbot Arena Elo (general quality)
  lmarena_coding  Chatbot Arena Coding Elo
  aaii            Artificial Analysis Intelligence Index v4 (aggregated)
  mmlu_pro        Knowledge composite
  gpqa            Graduate reasoning
  arc_agi         ARC-AGI reasoning
  gsm8k           Grade-school math
  hle             Humanity's Last Exam
  ifeval / math / bbh / humaneval — opportunistic from OpenEvals if present

Composition with the rest of the rotator stack:

    services.discovery.list_all_alive_models()      → {provider: [DiscoveryRecord,...]}
                          ↓
    services.benchmarks.canonicalize(provider_id)   → canonical_name
                          ↓
    services.benchmarks.get_benchmarks(canonical)   → {lmarena, aaii, ...}
                          ↓
    services.benchmarks.rank_for_step(step, alive)  → [(record, composite_score), ...]
                          ↓
    rotator builder materializes LiteLLM Router from the ranked list
                          ↓
    PILOT bandit (future) blends: α·benchmark + (1-α)·pilot_observed_score

Canonicalization (3 layers, descending speed / accuracy):
    L1 Heuristic strip — provider prefix, variant suffixes               (~0.1ms)
    L2 RapidFuzz token-set match against known canonicals                (~1ms)
    L3 HuggingFace model hub search (cached forever in Redis)            (~300ms)

Cache layout:
    kd:rotator:bench:lb:{source}          full leaderboard, 7d TTL
    kd:rotator:bench:scores:{canonical}   merged per-canonical scores, 90d TTL
    kd:rotator:bench:canonical:{prov_id}  provider_id → canonical_name, 1y TTL

OTel metrics emitted (per the rotator dashboard):
    kd.rotator_benchmark_fetch_total{source, outcome}    Counter
    kd.rotator_benchmark_fetch_duration_seconds{source}  Histogram
    kd.rotator_benchmark_cache_hit_total{layer}          Counter
    kd.rotator_canonical_resolution_total{layer}         Counter
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Awaitable, Callable

import httpx
import redis.asyncio as redis_aio

try:
    from rapidfuzz import fuzz as _rf_fuzz, process as _rf_process
    _RAPIDFUZZ_AVAILABLE = True
except ImportError:
    _RAPIDFUZZ_AVAILABLE = False

try:
    from bs4 import BeautifulSoup
    _BS4_AVAILABLE = True
except ImportError:
    _BS4_AVAILABLE = False

logger = logging.getLogger(__name__)


# =============================================================================
# Constants
# =============================================================================
CACHE_PREFIX_SCORES      = "kd:rotator:bench:scores:"
CACHE_PREFIX_LEADERBOARD = "kd:rotator:bench:lb:"
CACHE_PREFIX_CANONICAL   = "kd:rotator:bench:canonical:"

SCORES_TTL_S      = 90 * 24 * 3600
LEADERBOARD_TTL_S =  7 * 24 * 3600
CANONICAL_TTL_S   = 365 * 24 * 3600

HTTP_TIMEOUT_S = 30


# =============================================================================
# Per-step composite-score weights — uses metrics actually retrievable
# =============================================================================
STEP_WEIGHTS: dict[str, dict[str, float]] = {
    # code-heavy prose synthesis
    "kd-synth": {
        "lmarena_coding": 0.30,
        "lmarena":        0.25,
        "aaii":           0.20,
        "gpqa":           0.15,
        "mmlu_pro":       0.10,
    },
    # short structured-output classification
    "kd-reduce-label": {
        "lmarena":  0.35,
        "aaii":     0.30,
        "mmlu_pro": 0.20,
        "gpqa":     0.15,
    },
    # tiny instruct LMs — format adherence + small-model knowledge proxy
    "kd-keylm": {
        "mmlu_pro": 0.45,
        "aaii":     0.35,
        "gsm8k":    0.20,
    },
    # embeddings — no public MTEB-equivalent free source, fall back to general
    # quality. PILOT will diverge embedding ranking from chat ranking quickly.
    "kd-embed": {"lmarena": 1.0},
    # general fallback pool
    "kd-all": {
        "aaii":           0.30,
        "lmarena":        0.25,
        "lmarena_coding": 0.20,
        "mmlu_pro":       0.15,
        "gpqa":           0.10,
    },
    # planning — emphasize reasoning + instruction
    "kd-plan": {
        "lmarena":  0.30,
        "aaii":     0.30,
        "mmlu_pro": 0.20,
        "arc_agi":  0.20,
    },
    # curator — like synth, slightly lighter on code
    "kd-curator": {
        "lmarena":        0.35,
        "lmarena_coding": 0.25,
        "aaii":           0.20,
        "mmlu_pro":       0.20,
    },
    # grader / critic — knowledge + reasoning
    "kd-grader": {
        "aaii":     0.30,
        "lmarena":  0.25,
        "mmlu_pro": 0.20,
        "gpqa":     0.15,
        "hle":      0.10,
    },
    "kd-critic": {
        "aaii":     0.30,
        "lmarena":  0.25,
        "mmlu_pro": 0.20,
        "gpqa":     0.15,
        "hle":      0.10,
    },
}


# Provider-tier ordering — secondary sort key for tied (or unscored) models.
# When composite_score is identical (typically score==0 for models that no
# benchmark source covered), ties break by this order. Reflects empirical
# speed + reliability observations from the v1 catalog and 2026-04 production
# runs. PILOT will eventually override this with learned per-deployment data.
PROVIDER_TIER: dict[str, int] = {
    "groq":      1,    # LPU, sub-100ms TTFT, narrow but fast pool
    "cerebras":  2,    # WSE, fast, narrow pool
    "nim":       3,    # NVIDIA DGX Cloud — reliable, broadest catalog
    "mistral":   4,    # direct API, mid latency
    "gemini":    5,    # Google free tier — strict quotas
    "zhipu":     6,    # Chinese provider, geo-latency
    "sambanova": 7,
    "deepseek":  8,
}


# Normalization ranges — raw → [0, 1] (clipped)
SCORE_NORMS: dict[str, tuple[float, float]] = {
    "lmarena":        (700.0, 1500.0),
    "lmarena_coding": (700.0, 1600.0),
    "aaii":           (0.0, 100.0),
    "mmlu_pro":       (0.0, 100.0),
    "gpqa":           (0.0, 100.0),
    "arc_agi":        (0.0, 100.0),
    "gsm8k":          (0.0, 100.0),
    "hle":            (0.0, 100.0),
    "ifeval":         (0.0, 100.0),
    "math":           (0.0, 100.0),
    "bbh":            (0.0, 100.0),
    "humaneval":      (0.0, 100.0),
    "mteb":           (0.0, 100.0),
}


# =============================================================================
# Name normalization — heuristic layer 1
# =============================================================================
# Suffixes stripped to canonicalize variant names.
#
# RULE OF THUMB: strip TUNING/FORMAT/TIMESTAMP suffixes (these are different
# packagings of the same model), but PRESERVE SIZE/CAPABILITY suffixes (these
# are genuinely different models with different benchmark scores).
#
# DO STRIP — tuning, format, deployment-stage, version-stamp:
#   -instruct, -chat, -chat-it, -it       (instruction-tuned variants)
#   -versatile, -latest                   (Groq/Mistral marketing tags)
#   -preview, -preview-thinking           (release-stage flags)
#   -experimental, -instant               (release-stage flags)
#   -thinking, -reasoning                 (mode-switch flags; same weights underneath)
#   -2511, -2512, -2410, ...              (Mistral date stamps)
#
# DO NOT STRIP — size/capability identifiers (kept here for the negative-test
# documentation; removed from the active list):
#   -flash, -flash-lite                   (Gemini SIZE — flash ≠ pro)
#   -air                                  (Zhipu SIZE — glm-4.5-air ≠ glm-4.5)
#   -lite, -turbo                         (size/speed variants)
#   -nano, -mini, -small, -medium, -large (size identifiers; benchmark scores differ)
_PROVIDER_SUFFIXES = (
    "-2511", "-2512", "-2510", "-2509", "-2507", "-2410", "-2409", "-2408",
    "-versatile",
    "-latest",
    "-experimental",
    "-preview-thinking",
    "-preview",
    "-thinking",
    "-reasoning",
    "-instant",
    "-instruct",
    "-chat-it",
    "-chat",
    "-it",
)


def normalize_model_name(name: str) -> str:
    """Heuristic L1 normalizer. Examples:
      meta/llama-3.3-70b-instruct       → llama-3.3-70b
      llama-3.3-70b-versatile           → llama-3.3-70b
      moonshotai/kimi-k2.6              → kimi-k2.6
      z-ai/glm-5.1                      → glm-5.1
      models/gemini-2.5-pro             → gemini-2.5-pro
      "Mistral Large 3"                  → mistral-large-3   (OpenLM HTML cells)
    """
    s = (name or "").strip().lower()
    s = re.sub(r"^[^/]+/", "", s)
    # Convert whitespace runs to dashes — OpenLM table cells render some
    # entries with spaces ("Mistral Large 3"); discovery uses dashes. Aligning
    # both to the dash form unblocks ~40 Mistral/family entries that would
    # otherwise be unreachable. Run BEFORE suffix-stripping so that suffixes
    # delimited by spaces (rare but possible) still match.
    s = re.sub(r"\s+", "-", s)
    for _ in range(4):
        before = s
        for suffix in _PROVIDER_SUFFIXES:
            if s.endswith(suffix):
                s = s[: -len(suffix)]
                break
        if s == before:
            break
    s = re.sub(r"-+", "-", s)
    return s.strip("-")


# =============================================================================
# Canonicalization — layer 2 (RapidFuzz) + layer 3 (constrained HF API)
# =============================================================================
_known_canonicals: set[str] = set()

# Layer 3 (HF API search) ONLY fires when the provider_id begins with a
# recognizable HuggingFace organization prefix. This prevents proprietary
# closed-source models (Gemini, GLM, Kimi, MiniMax, DeepSeek-Pro) from being
# resolved to random HF community fine-tunes that happen to share a name
# token — the poisoning failure mode observed 2026-05-14 where Gemini got
# 0/12 coverage because HF search returned `google/gemma-2-9b-it` etc.
# Open-weights models hosted on HF DO have these prefixes in provider_id
# (e.g. `meta/llama-3.3-70b-instruct` on NIM), so they still benefit from L3.
_HF_FRIENDLY_PREFIXES = (
    "meta/", "meta-llama/",
    "mistralai/", "mistral/",
    "microsoft/",
    "google/",                      # gemma open weights, NOT gemini proprietary
    "openai/",                      # gpt-oss family on HF
    "deepseek-ai/",
    "qwen/", "alibaba/",
    "ibm-granite/", "ibm/",
    "snowflake/",
    "stabilityai/",
    "huggingfaceh4/", "huggingface/",
    "togethercomputer/",
    "writer/",
    "01-ai/",                       # yi family
    "bigcode/",
    "tiiuae/",
    "baai/",
)


async def canonicalize(
    provider_id: str,
    *,
    redis: redis_aio.Redis | None = None,
    fuzzy_threshold: int = 95,
) -> str:
    """Resolve a provider-specific id to a canonical name for benchmark lookup.

    fuzzy_threshold default 95 (was 85, raised 2026-05-14 after observing
    same-family false positives — e.g. token_set_ratio scored gemini-2.5-flash
    vs gemini-2.5-flash-lite at 86.5 (collapsing distinct size variants) and
    gemini-2.5-pro vs gemini-1.5-pro at 92.9 (collapsing wrong-generation
    models). 95 preserves legitimate typo/case-variation catches while
    rejecting same-family-different-variant collisions.
    """
    pid = (provider_id or "").strip()
    if not pid:
        return ""

    if redis is not None:
        try:
            cached = await redis.get(f"{CACHE_PREFIX_CANONICAL}{pid}")
            if cached:
                if isinstance(cached, bytes):
                    cached = cached.decode()
                _record_canonical("cache")
                _known_canonicals.add(cached)
                return cached
        except Exception as e:
            logger.debug(f"[bench] canonical cache read failed for {pid}: {e}")

    candidate = normalize_model_name(pid)
    resolved = candidate
    layer = "heuristic"

    if _RAPIDFUZZ_AVAILABLE and _known_canonicals:
        match = _rf_process.extractOne(
            candidate,
            list(_known_canonicals),
            scorer=_rf_fuzz.token_set_ratio,
        )
        if match and match[1] >= fuzzy_threshold:
            resolved = match[0]
            layer = "fuzzy"

    # Layer 3 — HF API fallback: DISABLED 2026-05-14.
    #
    # Rationale: HF model hub search ranks by `downloads`, which surfaces
    # quantized community variants (FP8, GGUF, AWQ, MLX, exl2) above the
    # canonical base model. Examples observed:
    #   meta/llama-4-maverick-17b-128e-instruct → ...-instruct-fp8 (poisoned)
    #   mistralai/mistral-large-3-675b-instruct-2512 → ...-gguf (poisoned)
    #
    # L1 (heuristic) + L2 (RapidFuzz threshold 95) handle every observed
    # canonicalization need without L3's failure mode. _HF_FRIENDLY_PREFIXES
    # is kept in module scope as documentation for future smarter layers.
    #
    # If we need to re-enable later, the right shape is: validate the HF
    # result's similarity to `candidate` AND prefer results whose `id` starts
    # with the same org as the original provider_id (filter quantizations).

    _known_canonicals.add(resolved)
    _record_canonical(layer)

    if redis is not None:
        try:
            await redis.set(
                f"{CACHE_PREFIX_CANONICAL}{pid}", resolved, ex=CANONICAL_TTL_S
            )
        except Exception as e:
            logger.debug(f"[bench] canonical cache write failed for {pid}: {e}")

    return resolved


async def _resolve_via_hf(query: str) -> str | None:
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://huggingface.co/api/models",
                params={"search": query, "limit": 1, "sort": "downloads"},
                timeout=10,
            )
            resp.raise_for_status()
            results = resp.json()
            if isinstance(results, list) and results:
                return results[0].get("id") or results[0].get("modelId")
    except Exception as e:
        logger.debug(f"[bench] HF resolve failed for {query}: {e}")
    return None


# =============================================================================
# Leaderboard fetchers
# =============================================================================
_inmem_leaderboards: dict[str, tuple[float, dict[str, dict[str, float]]]] = {}


async def _get_cached_leaderboard(
    source: str,
    fetcher: Callable[[httpx.AsyncClient], Awaitable[dict[str, dict[str, float]]]],
    redis: redis_aio.Redis | None,
    client: httpx.AsyncClient,
) -> dict[str, dict[str, float]]:
    """L1 in-mem → L2 Redis → fetch. Returns full leaderboard for one source."""
    now = time.time()

    cached = _inmem_leaderboards.get(source)
    if cached and (now - cached[0]) < LEADERBOARD_TTL_S:
        _record_cache_hit("inmem")
        return cached[1]

    if redis is not None:
        try:
            raw = await redis.get(f"{CACHE_PREFIX_LEADERBOARD}{source}")
            if raw:
                data = json.loads(raw if isinstance(raw, str) else raw.decode())
                _inmem_leaderboards[source] = (now, data)
                _record_cache_hit("redis_lb")
                return data
        except Exception as e:
            logger.debug(f"[bench] L2 read failed for {source}: {e}")

    t0 = time.time()
    try:
        data = await fetcher(client)
        outcome = "ok"
        logger.info(f"[bench] {source}: fetched {len(data)} models")
    except Exception as e:
        outcome = type(e).__name__
        logger.warning(
            f"[bench] {source} fetch failed: {outcome}: {str(e)[:200]}"
        )
        data = {}
    _record_fetch(source, outcome, time.time() - t0)

    _inmem_leaderboards[source] = (now, data)
    if redis is not None:
        try:
            ttl = LEADERBOARD_TTL_S if data else 3600
            await redis.set(
                f"{CACHE_PREFIX_LEADERBOARD}{source}", json.dumps(data), ex=ttl
            )
        except Exception as e:
            logger.debug(f"[bench] L2 write failed for {source}: {e}")
    return data


# ----- Source 1: OpenLM.ai Chatbot Arena+ (HTML scrape) ----------------------
# Column header → our metric key. Lowercased substring match.
_OPENLM_COLUMN_MAP: dict[str, str] = {
    "arena elo":     "lmarena",
    "arena score":   "lmarena",
    "coding":        "lmarena_coding",
    "vision":        "lmarena_vision",
    "aaii":          "aaii",
    "intelligence":  "aaii",
    "mmlu-pro":      "mmlu_pro",
    "mmlu pro":      "mmlu_pro",
    "arc-agi":       "arc_agi",
    "arc agi":       "arc_agi",
    "gpqa":          "gpqa",
}


def _parse_openlm_table(html: str) -> dict[str, dict[str, float]]:
    """Parse OpenLM.ai Chatbot Arena+ HTML; return {canonical: {metric: score}}.

    Defensive against table-structure drift — iterates all tables, picks the
    one whose header row contains at least 'Arena' and 'Model', then maps
    columns by header substring.
    """
    if not _BS4_AVAILABLE:
        return {}
    soup = BeautifulSoup(html, "html.parser")
    candidate_tables = soup.find_all("table")
    best_table = None
    best_headers: list[str] = []

    for tbl in candidate_tables:
        head = tbl.find("tr")
        if not head:
            continue
        headers = [
            (th.get_text(" ", strip=True) or "").lower()
            for th in head.find_all(["th", "td"])
        ]
        has_model = any("model" in h for h in headers)
        has_arena = any("arena" in h for h in headers)
        if has_model and has_arena and len(headers) > len(best_headers):
            best_table = tbl
            best_headers = headers

    if best_table is None:
        return {}

    # Identify the model-name column + metric columns
    model_col_idx = next(
        (i for i, h in enumerate(best_headers) if "model" in h), None
    )
    if model_col_idx is None:
        return {}

    metric_cols: list[tuple[int, str]] = []
    for idx, header in enumerate(best_headers):
        for substr, our_key in _OPENLM_COLUMN_MAP.items():
            if substr in header:
                metric_cols.append((idx, our_key))
                break

    out: dict[str, dict[str, float]] = {}
    for tr in best_table.find_all("tr")[1:]:        # skip header row
        cells = tr.find_all(["td", "th"])
        if len(cells) <= model_col_idx:
            continue
        name = cells[model_col_idx].get_text(" ", strip=True)
        if not name:
            continue
        scores: dict[str, float] = {}
        for col_idx, key in metric_cols:
            if col_idx >= len(cells):
                continue
            raw = cells[col_idx].get_text(" ", strip=True)
            v = _coerce_score(raw)
            if v is not None:
                scores[key] = v
        if scores:
            out[normalize_model_name(name)] = scores
    return out


def _coerce_score(raw: str) -> float | None:
    """Extract the first numeric value from a cell. Handles '1467', '87.1', '5.1+'."""
    if not raw or raw.strip() in ("—", "-", "—", "N/A", "TBD"):
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", raw.replace(",", ""))
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


async def _fetch_openlm_arena(client: httpx.AsyncClient) -> dict[str, dict[str, float]]:
    """Fetch OpenLM.ai Chatbot Arena+ and parse the table."""
    url = "https://openlm.ai/chatbot-arena/"
    resp = await client.get(
        url,
        headers={
            "User-Agent": "coelhonexus/1.0 (free-tier-rotator)",
            "Accept": "text/html,application/xhtml+xml",
        },
        timeout=HTTP_TIMEOUT_S,
        follow_redirects=True,
    )
    resp.raise_for_status()
    return _parse_openlm_table(resp.text)


# ----- Source 2: oolong-tea code.json (Chatbot Arena coding subset) ----------
async def _fetch_oolong_code(client: httpx.AsyncClient) -> dict[str, dict[str, float]]:
    """Fetch oolong-tea Arena code leaderboard. 2-step:
        latest.json (pointer) → data/{path}/code.json
    """
    headers = {"Accept": "application/json", "User-Agent": "coelhonexus/1.0"}
    base = "https://raw.githubusercontent.com/oolong-tea-2026/arena-ai-leaderboards/main/data"

    try:
        ptr = await client.get(
            f"{base}/latest.json", headers=headers, timeout=HTTP_TIMEOUT_S,
        )
        ptr.raise_for_status()
        pointer = ptr.json()
        snapshot_path = pointer.get("path") or pointer.get("date")
    except Exception as e:
        logger.warning(f"[bench] oolong latest pointer failed: {e}")
        return {}
    if not snapshot_path:
        return {}

    try:
        resp = await client.get(
            f"{base}/{snapshot_path}/code.json",
            headers=headers, timeout=HTTP_TIMEOUT_S,
        )
        resp.raise_for_status()
        body = resp.json()
    except Exception as e:
        logger.warning(f"[bench] oolong code.json fetch failed: {e}")
        return {}

    out: dict[str, dict[str, float]] = {}
    for item in body.get("models") or []:
        if not isinstance(item, dict):
            continue
        name = item.get("model") or item.get("name") or ""
        if not name:
            continue
        elo = item.get("score") or item.get("rating") or item.get("elo")
        if elo is None:
            continue
        try:
            out[normalize_model_name(str(name))] = {"lmarena_coding": float(elo)}
        except (TypeError, ValueError):
            continue
    return out


# ----- Source 3: OpenEvals/leaderboard-data (HF, open-weights fill-in) -------
# Schema is {benchmarks: {...}, models: [{...}, ...]} per agent research.
# Each model entry typically: {model_id, scores: {benchmark_key: value}}
# Map benchmark names → our metric keys.
_OPENEVALS_BENCHMARK_MAP: dict[str, str] = {
    "mmlu_pro":  "mmlu_pro",
    "mmlu-pro":  "mmlu_pro",
    "gpqa":      "gpqa",
    "gpqa_diamond": "gpqa",
    "gsm8k":     "gsm8k",
    "hle":       "hle",
    "humanity_last_exam": "hle",
    "humanity's_last_exam": "hle",
    "ifeval":    "ifeval",
    "math":      "math",
    "bbh":       "bbh",
    "humaneval": "humaneval",
}


def _normalize_openevals_key(key: str) -> str | None:
    """Map an OpenEvals benchmark column name → our metric key (or None)."""
    k = (key or "").strip().lower().replace("-", "_").replace(" ", "_")
    return _OPENEVALS_BENCHMARK_MAP.get(k)


async def _fetch_openevals(client: httpx.AsyncClient) -> dict[str, dict[str, float]]:
    """Fetch OpenEvals/leaderboard-data leaderboard.json from HuggingFace."""
    url = (
        "https://huggingface.co/datasets/OpenEvals/leaderboard-data/"
        "resolve/main/leaderboard.json"
    )
    resp = await client.get(
        url,
        headers={"Accept": "application/json", "User-Agent": "coelhonexus/1.0"},
        timeout=HTTP_TIMEOUT_S,
        follow_redirects=True,
    )
    resp.raise_for_status()
    body = resp.json()

    out: dict[str, dict[str, float]] = {}
    models = body.get("models") or body.get("results") or []
    for item in models:
        if not isinstance(item, dict):
            continue
        # Model identifier can be under several keys
        name = (
            item.get("model_id") or item.get("model") or item.get("name")
            or item.get("id") or ""
        )
        if not name:
            continue
        # Scores can be nested under "scores" or flat in the item
        scores_source = item.get("scores") or item.get("metrics") or item
        if not isinstance(scores_source, dict):
            continue

        scores: dict[str, float] = {}
        for raw_key, raw_val in scores_source.items():
            our_key = _normalize_openevals_key(raw_key)
            if our_key is None:
                continue
            # Value may be a dict {value, confidence} or a number
            if isinstance(raw_val, dict):
                raw_val = raw_val.get("value") or raw_val.get("score")
            if raw_val is None:
                continue
            try:
                scores[our_key] = float(raw_val)
            except (TypeError, ValueError):
                continue
        if scores:
            out[normalize_model_name(str(name))] = scores
    return out


# =============================================================================
# Sources table
# =============================================================================
_SOURCES: dict[str, Callable[[httpx.AsyncClient], Awaitable[dict[str, dict[str, float]]]]] = {
    "openlm_arena":  _fetch_openlm_arena,
    "oolong_code":   _fetch_oolong_code,
    "openevals":     _fetch_openevals,
}


# =============================================================================
# Aggregator
# =============================================================================
async def get_benchmarks(
    canonical_name: str,
    *,
    redis: redis_aio.Redis | None = None,
) -> dict[str, float]:
    """Return merged benchmark scores for a canonical model name.

    L3 Redis cache hit → return.
    Cache miss → fan out to all _SOURCES in parallel (each with its own L1/L2),
    merge, cache 90 days. Returns {} for unknown models.
    """
    canonical = (canonical_name or "").strip().lower()
    if not canonical:
        return {}

    if redis is not None:
        try:
            cached = await redis.get(f"{CACHE_PREFIX_SCORES}{canonical}")
            if cached:
                _record_cache_hit("scores")
                return json.loads(
                    cached if isinstance(cached, str) else cached.decode()
                )
        except Exception as e:
            logger.debug(f"[bench] L3 read failed for {canonical}: {e}")

    async with httpx.AsyncClient() as client:
        boards = await asyncio.gather(
            *[
                _get_cached_leaderboard(name, fetcher, redis, client)
                for name, fetcher in _SOURCES.items()
            ],
            return_exceptions=True,
        )

    merged: dict[str, float] = {}
    for result in boards:
        if isinstance(result, Exception) or not isinstance(result, dict):
            continue
        per_model = result.get(canonical, {})
        merged.update(per_model)

    if redis is not None:
        try:
            ttl = SCORES_TTL_S if merged else 3600
            await redis.set(
                f"{CACHE_PREFIX_SCORES}{canonical}", json.dumps(merged), ex=ttl
            )
        except Exception as e:
            logger.debug(f"[bench] L3 write failed for {canonical}: {e}")
    return merged


# =============================================================================
# Scoring
# =============================================================================
def _normalize_metric(metric: str, raw: float) -> float:
    """Linear → [0, 1], clipping out-of-range."""
    lo, hi = SCORE_NORMS.get(metric, (0.0, 1.0))
    if hi <= lo:
        return 0.0
    return max(0.0, min(1.0, (raw - lo) / (hi - lo)))


def compute_composite_score(
    scores: dict[str, float],
    weights: dict[str, float],
) -> float:
    """Weighted average of normalized scores. Missing metric contributes 0.

    Denominator = sum of weights for PRESENT metrics, so a model with only
    lmarena Elo is comparable to one with lmarena + AAII (average over what's
    known).
    """
    if not scores or not weights:
        return 0.0
    weighted_sum = 0.0
    weight_total = 0.0
    for metric, weight in weights.items():
        raw = scores.get(metric)
        if raw is None:
            continue
        norm = _normalize_metric(metric, raw)
        weighted_sum += weight * norm
        weight_total += weight
    if weight_total == 0.0:
        return 0.0
    return weighted_sum / weight_total


def compute_warm_start_score(
    benchmark_score: float,
    pilot_score: float | None,
    *,
    alpha: float,
) -> float:
    """Blend benchmark prior with PILOT-learned posterior.

    alpha=1.0 → pure benchmark prior (cold start, no observations)
    alpha=0.0 → pure PILOT posterior (steady state, observations dominate)

    Future PILOT integration plugs in here; until then pilot_score is None and
    the function returns the benchmark_score unchanged.
    """
    a = max(0.0, min(1.0, alpha))
    if pilot_score is None:
        return benchmark_score
    return a * benchmark_score + (1.0 - a) * pilot_score


# =============================================================================
# Public ranking API
# =============================================================================
async def rank_for_step(
    step: str,
    alive_models: list,
    *,
    redis: redis_aio.Redis | None = None,
) -> list[tuple[Any, float]]:
    """Rank discovered free-tier models for a step by composite benchmark score.

    Returns [(record, composite_score), ...] sorted descending. Models with no
    benchmark coverage get score 0.0 and land at the end.

    Performance: fetches all 3 source leaderboards ONCE (single httpx client,
    parallel gather), then does in-memory lookups for all N canonicals. The
    naive O(N×3) per-canonical fan-out was OOM-pressuring the pod when called
    with limit=250+ on cold cache; this is O(N + 3).
    """
    weights = STEP_WEIGHTS.get(step, STEP_WEIGHTS["kd-all"])
    if not alive_models:
        return []

    # Canonicalize all model IDs in parallel (no network calls after L3 disable;
    # this is essentially N regex strips + N redis canonical-cache reads).
    canonicals = await asyncio.gather(
        *[canonicalize(getattr(m, "model_id", ""), redis=redis)
          for m in alive_models]
    )

    # Fetch all benchmark leaderboards ONCE — single httpx client, parallel
    # across sources. Each fetcher uses its own L1 in-mem + L2 Redis cache.
    async with httpx.AsyncClient() as client:
        board_results = await asyncio.gather(
            *[
                _get_cached_leaderboard(name, fetcher, redis, client)
                for name, fetcher in _SOURCES.items()
            ],
            return_exceptions=True,
        )
    valid_boards: list[dict[str, dict[str, float]]] = [
        b for b in board_results
        if isinstance(b, dict)
    ]

    # In-memory merge per canonical (no further network/Redis traffic).
    def _merge_for(canonical: str) -> dict[str, float]:
        merged: dict[str, float] = {}
        for board in valid_boards:
            per_model = board.get(canonical, {})
            if per_model:
                merged.update(per_model)
        return merged

    ranked: list[tuple[Any, float]] = []
    for record, canonical in zip(alive_models, canonicals):
        scores = _merge_for(canonical)
        composite = compute_composite_score(scores, weights)
        ranked.append((record, composite))

    # Multi-key sort:
    #   primary  — composite_score (descending, so a scored model always
    #              outranks an unscored one regardless of provider tier)
    #   secondary — provider tier (ascending: groq=1 first, zhipu=6 last)
    #   tertiary — model_id (alphabetical, for determinism across runs)
    # The secondary key is what gives unscored tied-at-zero models a sensible
    # initial ordering until PILOT learns the real per-deployment posterior.
    ranked.sort(
        key=lambda x: (
            -x[1],
            PROVIDER_TIER.get(getattr(x[0], "provider", ""), 99),
            getattr(x[0], "model_id", ""),
        )
    )
    return ranked


# =============================================================================
# OTel metrics
# =============================================================================
_metric_instruments: dict[str, Any] = {}


def _ensure_metrics() -> dict[str, Any]:
    if _metric_instruments:
        return _metric_instruments
    try:
        from services.llm.otel_setup import get_meter
        meter = get_meter()
        if meter is None:
            return _metric_instruments
        _metric_instruments["fetch_counter"] = meter.create_counter(
            name="kd.rotator_benchmark_fetch_total",
            description="Benchmark leaderboard fetches — labels: source, outcome",
        )
        _metric_instruments["fetch_duration"] = meter.create_histogram(
            name="kd.rotator_benchmark_fetch_duration_seconds",
            description="Per-source leaderboard fetch wall-clock",
            unit="s",
        )
        _metric_instruments["cache_hit"] = meter.create_counter(
            name="kd.rotator_benchmark_cache_hit_total",
            description="Cache hits — labels: layer ∈ {inmem, redis_lb, scores, canonical}",
        )
        _metric_instruments["canonical_counter"] = meter.create_counter(
            name="kd.rotator_canonical_resolution_total",
            description="Canonicalization resolutions — labels: layer ∈ {cache, heuristic, fuzzy, hf_api}",
        )
        logger.info(f"[bench] {len(_metric_instruments)} OTel instruments registered")
    except Exception as e:
        logger.warning(f"[bench] OTel init failed: {type(e).__name__}: {e}")
    return _metric_instruments


def _record_fetch(source: str, outcome: str, duration_s: float) -> None:
    inst = _ensure_metrics()
    try:
        if "fetch_counter" in inst:
            inst["fetch_counter"].add(1, attributes={"source": source, "outcome": outcome})
        if "fetch_duration" in inst:
            inst["fetch_duration"].record(duration_s, attributes={"source": source})
    except Exception:
        pass


def _record_cache_hit(layer: str) -> None:
    inst = _ensure_metrics()
    try:
        if "cache_hit" in inst:
            inst["cache_hit"].add(1, attributes={"layer": layer})
    except Exception:
        pass


def _record_canonical(layer: str) -> None:
    inst = _ensure_metrics()
    try:
        if "canonical_counter" in inst:
            inst["canonical_counter"].add(1, attributes={"layer": layer})
    except Exception:
        pass
