from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # type-only — never imported at runtime (no circular import)
    from litellm import Router
    from langchain_litellm.chat_models import ChatLiteLLMRouter

GROUP = "dd-all"
KEYLM_GROUP = "dd-keylm"
REDUCE_LABEL_GROUP = "dd-reduce-label"
SYNTH_GROUP = "dd-synth"
DD_EMBED_GROUP = "dd-embed"
# 2026-05-23 night — REVERTED Phase B embedder swap. `llama-embed-nemotron-8b`
# (which the research agent recommended) is NOT exposed at
# integrate.api.nvidia.com/v1/embeddings as of this date — NIM returned 404
# during the FastMCP Planner run, breaking embed_corpus. The model exists on
# build.nvidia.com (the catalog UI) and HuggingFace, but not on the production
# embedding endpoint we route through.
#
# Verified-available NIM embedders with 32K context (via /v1/models probe):
#   - nvidia/nv-embed-v1                  4096-D, Mistral-7B base
#   - nvidia/nv-embedqa-mistral-7b-v2     4096-D, QA-tuned Mistral
# Both untested; switching requires a separate verification pass (probe with
# a real /v1/embeddings call before committing in code). Until then, sticking
# with the proven baseline.
DD_EMBED_MODEL_NAME = "nvidia/llama-nemotron-embed-1b-v2"
# Hard upper bound on inputs per /v1/embeddings call. NIM doesn't publish a
# strict limit; 64 is empirically safe and matches the previous Xinference
# batch tuning. Helper functions auto-batch above this.
DD_EMBED_BATCH_SIZE = 64
DD_RERANK_MODEL_NAME = "nvidia/llama-nemotron-rerank-1b-v2"
_NIM_RERANK_BASE = "https://ai.api.nvidia.com/v1/retrieval"
# We use dd_process="dd-grader" (not "dd-all") so the bandit cells for the
# judge stay separate from synthesizer cells — different reward shapes
# (binary vs continuous), different latency expectations, different
# preferred models. Empty dd-grader cells warm-start from benchmark priors.
_JUDGE_KD_PROCESS = "dd-grader"
# Expected wall per judge call. Used by compose_reward's latency component:
# faster than this → positive contribution, slower → negative.
_JUDGE_EXPECTED_LATENCY_S = 4.0
# How many ranked deployments to cascade through before giving up. Matches
# the bandit-driven chapter-pin cascade pattern in pick_synth_deployment_bandit.
# 2026-05-27 P2 — bumped 5 → 10 after planner Claude Code Run showed
# "all 5 ranked deployments failed" RuntimeError in 36% of doc_distill
# calls (NIM + Mistral burst-saturated, bandit's top-5 all 429'd).
# Top-10 cascade gives access to broader provider mix (Groq Llama-3.3-
# 70B, Mistral Small, Llama-4 Maverick on NIM) when top-5 are throttled.
_JUDGE_BANDIT_TOP_K = 10
_PROVIDER_CHAPTER_CAPS: dict[str, int] = {
    "nvidia_nim": 2,
    "groq":       2,
    "cerebras":   2,
    "mistral":    3,
    "gemini":     1,
}
# =============================================================================
# DYNAMIC CATALOG — discovery + benchmarks → top-K per step (Phase 1, 2026-05-14)
# =============================================================================
# Per-step top-K — picks the highest-benchmark slice of the discovered pool.
# Larger K = more cascade depth + more cooldown redundancy; smaller K = tighter
# rotator decisions. Calibrated against the v1 static catalog sizes.
_DYNAMIC_TOP_K: dict[str, int] = {
    "dd-all":           30,
    "dd-synth":         12,
    "dd-reduce-label":  10,
}

# Per-step group name + default per-deployment timeout (s). Reasoning-heavy
# pools need longer; classification pools shorter.
_DYNAMIC_STEP_TO_GROUP: dict[str, str] = {
    "dd-all":           "dd-all",
    "dd-synth":         "dd-synth",
    "dd-reduce-label":  "dd-reduce-label",
}
_DYNAMIC_STEP_TIMEOUT_S: dict[str, int] = {
    "dd-all":           120,
    "dd-synth":         180,    # reasoning models burn <think> tokens
    "dd-reduce-label":   90,    # non-reasoning, fast
}

# Quality floor for "All free" model selection (2026-05-31). Live discovery
# returns EVERY free model a provider hosts — including tiny ones (gemma-2-2b,
# granite-8b, gemma-3n-e4b) that can't reliably emit the strict structured
# output the heavy pools need (doc_distill JSON distillates, chapter_assign,
# SAWC). Those produced ~12% deterministic-fallback distillates on the FastAPI
# planner run. So for the heavy structured-generation pools we drop discovered
# models below a parameter-size floor UNLESS they're MoE (capable despite low
# active params) or the user CUSTOM-selected them (explicit choice always wins).
# dd-reduce-label is exempt — small fast models are fine for ordering/labels.
# Override the floor via env KD_DYNAMIC_MIN_PARAM_B (e.g. 0 = include all,
# 70 = only the largest) for testing.
_DYNAMIC_MIN_PARAM_B: float = 20.0
_DYNAMIC_QUALITY_FLOOR_STEPS: frozenset = frozenset({"dd-all", "dd-synth"})


_router_instance: Router | None = None
# Per-process cache so we don't build a new Router for every chapter call.
# Keyed by the pinned litellm model string. ChatLiteLLMRouter wraps a Router
# under the hood; one cache per process is the right scope (Celery prefork
# workers each have their own).
_pinned_chain_cache: dict[str, "ChatLiteLLMRouter"] = {}
# Pinned-group → parent-group registry (Phase 3 fix, 2026-05-14).
# When build_synth_pinned_chain / build_pinned_chain_any wraps a deployment
# in a single-entry Router, the resulting chain's `.model` attribute is the
# hashed pinned group (e.g. "dd-synth-pinned-abc123"). Downstream callers
# (helpers.py per-call cascade) need the PARENT pool name to enumerate
# alternative candidates — without this, the cascade collapses to k=1 and
# can't escape a failing chapter pin. See canary v4 evidence in
# docs/KD-NEXT-STEPS-2026-05-14.md.
_pinned_to_parent: dict[str, str] = {}
_dynamic_entries: dict[str, list[dict]] = {}
_dynamic_catalog_initialized: bool = False
