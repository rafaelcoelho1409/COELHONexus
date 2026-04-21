"""
Central LLM Fallback Chain

ONE source of truth for the ordered Groq + NVIDIA NIM model list used by
every graph (YouTube Adaptive RAG, Knowledge Distiller, exports, scope gate).
Previously this list was duplicated in app.py (FastAPI lifespan) and in
tasks/knowledge/distiller.py's _build_llm_chain — duplicates drift, and
we already hit a case where both copies still referenced models that had
been deprecated.

ORDERING PRINCIPLES (April 2026 — re-review every quarter):
  Phase 1 — Large context (>128K) for KD planner / synthesizer on big corpora.
            If the primary fails, we want the next model to ALSO have enough
            context, or the whole fallback chain no-ops on a 413.
  Phase 2 — 128K context, quality-sorted by Arena ELO.
  Phase 3 — Speed-first fallbacks (Groq tiny models) for classifier-style calls.
  Provider interleaved so a single-provider outage (Groq down OR NIM down)
  still leaves working options one position away.

DEPRECATIONS (as of 2026-04-20, must remove from chain):
  - z-ai/glm5          (NIM, EoL 2026-04-20) → use z-ai/glm-4.7
  - moonshotai/kimi-k2-instruct (Groq, EoL 2026-04-15) → use NIM's kimi-k2.5-thinking

KNOWN QUIRKS (research/NIM forum bug 366612, 2026-04-14):
  - NIM may return malformed JSON in tool_calls (dict vs string args,
    Python literals, trailing commas). `with_structured_output` can raise;
    LangChain's `with_fallbacks` picks up the next model. Acceptable.
  - Groq llama-3.3-70b-versatile has the most reliable function_calling of
    any model in the chain — keep it in the middle of Phase 2 as a strong
    function-calling-specific fallback.

TIMEOUTS (tuned 2026-04-20 for quality-first background runs):
  Priority: get the FIRST model (GLM-5.1, best quality) to actually complete,
  not timeout and cascade to lower-quality fallbacks. Reasoning models
  legitimately think for minutes on structured-output calls over ~25K-token
  planner prompts — cutting them short wastes the quality advantage.

  - Groq: 120s HTTP / 120s Celery — Groq is fast (hello-world <5s); 120s is
          safety padding for long chapter synthesis generations.
  - NIM:  300s HTTP / 420s Celery — reasoning models (GLM-5.1, Kimi K2.5,
          Qwen3.5-397B) can take 2-5 min on a 25K-token planner prompt with
          function_calling output. 300s is the sweet spot between "primary
          actually completes" and "broken model doesn't stall the chain
          forever". Celery gets +2 min since tasks are backgrounded.

  Worst-case chain walk: 14 models × ~200s avg = ~45 min. But the primary
  should usually succeed in 1-3 min, so real p50 is well under that.

FLAKY MODELS (observed 2026-04-20 — kept but demoted):
  - moonshotai/kimi-k2.5 — one hello-world timeout at 25s during live testing,
    but quality is top-tier when it works (262K ctx, strong reasoning). Placed
    at position 5 (after the consistently fast primaries) so one stuck call
    is a 90s blip, not a chain-killer.
"""
import os
from langchain_openai import ChatOpenAI


# =============================================================================
# Endpoints
# =============================================================================
GROQ_URL = "https://api.groq.com/openai/v1"
NVIDIA_URL = "https://integrate.api.nvidia.com/v1"


# =============================================================================
# Model catalog (edit THIS file when deprecations happen)
# =============================================================================
# Each list is ordered by the research-derived "best first" priority.
# Tuples: (model_id, rationale_tag) — the tag is informational, not consumed.

# --- Groq: speed-first; 128K ctx max except llama-4-scout (512K on Groq) ---
GROQ_CATALOG: list[tuple[str, str]] = [
    ("meta-llama/llama-4-scout-17b-16e-instruct", "groq-512k"),    # 512K ctx on Groq — top choice for big planner prompts
    ("openai/gpt-oss-120b",                       "groq-flag"),    # ELO 1368, 500 TPS — Groq flagship after Kimi deprecation
    ("qwen/qwen3-32b",                            "groq-reason"),  # ELO 1342, 131K ctx, 196–662 TPS
    ("llama-3.3-70b-versatile",                   "groq-tools"),   # Best function_calling reliability on Groq
    ("openai/gpt-oss-20b",                        "groq-fast"),    # 1000 TPS, 128K ctx — speed fallback
    ("llama-3.1-8b-instant",                      "groq-tiny"),    # 840 TPS, cheapest; scope-gate primary
]

# --- NVIDIA NIM: quality-first; 40 RPM free tier per model ---
# All IDs below verified via GET /v1/models on 2026-04-20.
# Research agent hallucinated several names (-thinking suffixes, etc.) that
# don't exist — corrected here.
NIM_CATALOG: list[tuple[str, str]] = [
    ("z-ai/glm-5.1",                              "nim-top"),      # Latest GLM (replaces glm5 EoL'd 2026-04-20); 1.4s hello
    ("qwen/qwen3.5-397b-a17b",                    "nim-structured"),  # 262K ctx; 2.7s hello; best structured output
    ("moonshotai/kimi-k2.5",                      "nim-reason"),   # 262K ctx, top reasoning (flaky 2026-04-20 — demoted, see header)
    ("z-ai/glm4.7",                               "nim-code-fb"),  # 1.1s hello; GLM fallback; MIT
    ("nvidia/nemotron-3-super-120b-a12b",         "nim-context"),  # 1M ctx insurance; 4.1s hello; highest NIM throughput
    ("deepseek-ai/deepseek-v3.2",                 "nim-v3"),       # ELO 1422, 128K ctx
    ("minimaxai/minimax-m2.7",                    "nim-agent"),    # 204K ctx, agentic coding (non-commercial license)
    ("mistralai/mistral-large-3-675b-instruct-2512", "nim-general"),  # Reliable generalist, 128K ctx
]


# =============================================================================
# Factory
# =============================================================================
def _groq(model: str, timeout_s: int) -> ChatOpenAI:
    return ChatOpenAI(
        model = model,
        temperature = 0.0,
        base_url = GROQ_URL,
        api_key = os.environ.get("GROQ_API_KEY", ""),
        max_retries = 0,
        timeout = timeout_s,
    )


def _nim(model: str, timeout_s: int) -> ChatOpenAI:
    return ChatOpenAI(
        model = model,
        temperature = 0.0,
        base_url = NVIDIA_URL,
        api_key = os.environ.get("NVIDIA_API_KEY", ""),
        max_retries = 0,
        timeout = timeout_s,
    )


def _ordered_fallback(
    groq_timeout_s: int,
    nim_timeout_s: int) -> list[ChatOpenAI]:
    """
    Interleaved, phase-grouped fallback chain.

    Provider interleaving is intentional: if Groq is rate-limited, the next
    model is NIM, and vice versa. A correlated failure (one provider down)
    doesn't require walking 5+ entries before finding an alive endpoint.
    """
    g = {tag: _groq(m, groq_timeout_s) for m, tag in GROQ_CATALOG}
    n = {tag: _nim(m, nim_timeout_s) for m, tag in NIM_CATALOG}
    ordered = [
        # Phase 1 — Large context (>128K), verified fast primaries first
        n["nim-top"],         # GLM-5.1 — NIM flagship, 1.4s
        n["nim-structured"],  # Qwen 3.5 397B — 262K ctx, 2.7s, best structured output
        g["groq-512k"],       # Llama 4 Scout — 512K ctx on Groq, 0.6s
        n["nim-context"],     # Nemotron-3 Super — 1M ctx insurance, 4.1s
        n["nim-reason"],      # Kimi K2.5 — 262K, top reasoning when healthy (flaky)
        n["nim-agent"],       # Minimax M2.7 — 204K ctx, agentic
        # Phase 2 — 128K context, quality-sorted
        n["nim-code-fb"],     # GLM 4.7 — 1.1s, GLM fallback
        n["nim-v3"],          # DeepSeek V3.2 — ELO 1422
        g["groq-flag"],       # GPT-OSS 120B — 0.5s, ELO 1368, 500 TPS
        g["groq-reason"],     # Qwen3 32B — 4.6s (<think> preamble), 131K
        g["groq-tools"],      # Llama 3.3 70B — best function-calling on Groq
        n["nim-general"],     # Mistral Large 3 — reliable generalist
        # Phase 3 — Speed fallbacks (classification / short prompts)
        g["groq-fast"],       # GPT-OSS 20B — 1000 TPS
        g["groq-tiny"],       # Llama 3.1 8B — 840 TPS (final fallback)
    ]
    return ordered


def build_llm_fallback_chain(
    groq_timeout_s: int = 120,
    nim_timeout_s: int = 300):
    """
    Return a single RunnableWithFallbacks that auto-rotates through every
    provider+model. The caller gets ONE object; LangChain handles retry
    semantics (next model on exception — timeout, 429, 413, malformed
    tool_call JSON, etc.).

    Per-call behavior:
      - Groq attempt: up to `groq_timeout_s` (default 90s) per model
      - NIM attempt:  up to `nim_timeout_s` (default 240s) per model
      - max_retries=0 on each → no internal retry loop; fallback handles it

    Missing API keys: the ChatOpenAI instance is still constructed (empty
    key), but requests will fail with 401 and fall through to the next model.
    Production MUST have both GROQ_API_KEY and NVIDIA_API_KEY set.
    """
    chain = _ordered_fallback(groq_timeout_s, nim_timeout_s)
    primary = chain[0]
    fallbacks = chain[1:]
    return primary.with_fallbacks(fallbacks)


def build_synth_fallback_chain(
    groq_timeout_s: int = 120,
    nim_timeout_s: int = 300):
    """
    Synthesis-only chain — EXCLUDES the Groq tail (`llama-3.3-70b-versatile`
    and `llama-3.1-8b-instant`).

    Why a separate chain: research (Nature Communications 2025 agent
    benchmark) measured llama-3.3-70B at 32% code-gen error rate; llama-3.1-8B
    is significantly worse on structured output and code-heavy synthesis.
    Accepting a chapter that cascaded to either would poison the study.

    Used by:
      - synthesize_chapter (authoritative per-chapter output)
      - curator (final style-normalization pass)

    NOT used by:
      - scope classifier (llama-3.1-8b-instant is perfect there — cheap, fast)
      - LLM disambiguation in /resolve (cheap classifier-class task)

    Falls back to the full chain's tail ONLY if every primary is down — in
    practice, seven NIM models + four Groq mid-tier models provide enormous
    headroom and the tail almost never fires.
    """
    chain = _ordered_fallback(groq_timeout_s, nim_timeout_s)
    # Drop the last two entries (Groq gpt-oss-20b + llama-3.1-8b-instant).
    # gpt-oss-20b stays — it's Apache 2.0 with good FC reliability (OpenAI claims
    # parity with o3-mini). llama-3.1-8b-instant is the one to hard-exclude.
    filtered: list[ChatOpenAI] = []
    for entry in chain:
        # ChatOpenAI stores the model under `model_name`; filter by model_id string
        model_id = getattr(entry, "model_name", None) or getattr(entry, "model", "")
        if model_id == "llama-3.1-8b-instant":
            continue  # tail exclusion for synth quality
        if model_id == "llama-3.3-70b-versatile":
            continue  # tail exclusion — 32% code-gen error in benchmarks
        filtered.append(entry)
    primary = filtered[0]
    fallbacks = filtered[1:]
    return primary.with_fallbacks(fallbacks)


def build_refine_llm_chain(
    groq_timeout_s: int = 120,
    nim_timeout_s: int = 300):
    """
    Refiner chain with T=0.7 for Self-Refine iterations.

    Research (2026-04-21):
      - Madaan et al. 2023 Self-Refine (arxiv 2303.17651v2) used T=0.7 for
        critique/refine; T=0 collapses exploration and commits to a single
        deterministic edit path. Known cause of iter N < iter N-1 regression.
      - Huang et al. 2024 ICLR (arxiv 2310.01798v2 §3.3) documents regression
        frequency with deterministic refinement; higher temperature gives the
        refiner alternative edit paths to escape the "one wrong fix" trap.

    Grader stays at T=0 (2506.05234: judge-side determinism matters more).
    Synthesizer also stays at T=0 for initial deterministic synthesis —
    only the ADJUSTMENT-GENERATION + subsequent re-synthesize steps benefit
    from exploration.

    Used only for the `_generate_adjustment` + refine re-synthesize call
    path inside the Self-Refine loop; other graph nodes use the canonical
    `build_llm_fallback_chain()`.
    """
    # Rebuild the ordered chain with T=0.7 for both Groq + NIM models
    def _groq_t07(model: str) -> ChatOpenAI:
        return ChatOpenAI(
            model = model,
            temperature = 0.7,
            base_url = GROQ_URL,
            api_key = os.environ.get("GROQ_API_KEY", ""),
            max_retries = 0,
            timeout = groq_timeout_s,
        )
    def _nim_t07(model: str) -> ChatOpenAI:
        return ChatOpenAI(
            model = model,
            temperature = 0.7,
            base_url = NVIDIA_URL,
            api_key = os.environ.get("NVIDIA_API_KEY", ""),
            max_retries = 0,
            timeout = nim_timeout_s,
        )
    g = {tag: _groq_t07(m) for m, tag in GROQ_CATALOG}
    n = {tag: _nim_t07(m) for m, tag in NIM_CATALOG}
    ordered = [
        n["nim-top"], n["nim-structured"], g["groq-512k"],
        n["nim-context"], n["nim-reason"], n["nim-agent"],
        n["nim-code-fb"], n["nim-v3"],
        g["groq-flag"], g["groq-reason"],
        # Drop llama-3.3-70b-versatile + llama-3.1-8b-instant (synth-quality exclusions)
        n["nim-general"],
    ]
    primary = ordered[0]
    fallbacks = ordered[1:]
    return primary.with_fallbacks(fallbacks)


def build_curator_llm(timeout_s: int = 600):
    """
    Pin the curator to ONE model — research (Mixture-of-Agents, arXiv
    2406.04692) says a single aggregator over heterogeneous proposers is the
    pattern that works. Rotating the curator defeats its purpose.

    Pick: z-ai/glm-5.1 on NIM. It's the primary of our quality chain, has
    200K context (enough for a full multi-chapter study), strong structured-
    output discipline, MIT license, and the best Terminal-Bench 2.0 score
    among open models.

    Timeout: 10 minutes. Curator processes chapters one at a time, and a
    262K-context reasoning call can legitimately take several minutes.
    """
    return _nim("z-ai/glm-5.1", timeout_s)


def build_scope_classifier_llm(timeout_s: int = 30):
    """
    Dedicated lightweight classifier for the scope-gate (~500ms binary).
    Groq llama-3.1-8b-instant primary; falls back to the main chain if Groq
    is down or key is missing.

    Kept separate from the main chain because:
      - Scope gate is sync-path on POST /studies — latency matters.
      - Using the main chain (starts with NIM 262K reasoning model) would
        spend seconds thinking about "is pydantic a code framework?"
    """
    groq_key = os.environ.get("GROQ_API_KEY", "")
    if groq_key:
        primary = _groq("llama-3.1-8b-instant", timeout_s)
        return primary.with_fallbacks([build_llm_fallback_chain()])
    # Local dev without Groq key — fall back to the main chain
    return build_llm_fallback_chain()
