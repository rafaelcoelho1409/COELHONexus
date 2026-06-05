from __future__ import annotations

from .keys import (
    DD_SYNTH_WRITE_HEAVYWEIGHTS,
    _LITELLM_PREFIX_TO_PROVIDER,
    _NON_CHAT_MARKERS,
    _PROVIDER_KEY_ENV,
)
from .patterns import (
    MOE_RE, 
    PARAM_SIZE_RE
)


# --------------------------------------------------------------------------- #
# Error → bandit reward class
# --------------------------------------------------------------------------- #
def classify_error(exc: Exception) -> str:
    """Map a litellm/httpx exception to ParetoBandit's error_class taxonomy
    (see compose_reward in bandit.py for the reward magnitudes)."""
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    if "ratelimit" in name or "429" in msg or "rate limit" in msg:
        return "rate_limit"
    if "timeout" in name or "timed out" in msg:
        return "timeout"
    if "auth" in name or "401" in msg or "403" in msg or "invalid api key" in msg:
        return "auth_error"
    if "content" in name and "filter" in name:
        return "content_filter"
    if "5" in msg and ("server" in msg or "internal" in msg or "bad gateway" in msg):
        return "server_error"
    return "unknown"


# --------------------------------------------------------------------------- #
# Model classification
# --------------------------------------------------------------------------- #
def is_heavyweight(deployment_id: str) -> bool:
    """True if `deployment_id` is on the SAWC-writer heavyweight whitelist."""
    return any(s in deployment_id for s in DD_SYNTH_WRITE_HEAVYWEIGHTS)


def is_non_chat_model(model_id: str) -> bool:
    """True for embedder/reranker/vision-encoder/ASR/TTS/classifier/reward
    models — never valid in a chat pool. Provider-agnostic."""
    name = (model_id or "").lower()
    return any(m in name for m in _NON_CHAT_MARKERS)


def passes_capability_floor(model_id: str, min_b: float) -> bool:
    """True if a discovered model is large enough for strict structured
    generation. MoE → always True. Else True iff the largest '<N>b' token is
    >= min_b. No parseable size → True (newer-named frontier model)."""
    if min_b <= 0:
        return True
    name = (model_id or "").lower()
    if MOE_RE.search(name):
        return True
    sizes = [float(x) for x in PARAM_SIZE_RE.findall(name)]
    if sizes:
        return max(sizes) >= min_b
    return True


# --------------------------------------------------------------------------- #
# Provider id ↔ env var name
# --------------------------------------------------------------------------- #
def provider_key_env(provider: str) -> str:
    """LiteLLM provider prefix → env-var name. Falls back to NVIDIA_API_KEY
    so the cascade still attempts with the most-likely-set key."""
    return _PROVIDER_KEY_ENV.get(provider, "NVIDIA_API_KEY")


def entry_provider_and_model(entry: dict) -> tuple[str, str]:
    """Extract (registry_provider_id, model_id) from a LiteLLM entry dict.
    Registry id matches what settings.json uses."""
    m = (entry.get("litellm_params") or {}).get("model", "")
    prefix, _, model = m.partition("/")
    return _LITELLM_PREFIX_TO_PROVIDER.get(prefix, prefix), model


# --------------------------------------------------------------------------- #
# BYOK selection predicates
# --------------------------------------------------------------------------- #
def provider_mode(provider_id: str, sel: dict) -> str:
    """'all' (use every free model, opt-in new) or 'custom' (only selected)."""
    return (sel.get("mode") or {}).get(provider_id, "all")


def selection_allows(provider_id: str, model_id: str, sel: dict) -> bool:
    """Canonical BYOK predicate — shared by the entry filter (static/dynamic
    catalog) AND the discovery-record path (dynamic catalog build). Provider
    ids here are REGISTRY ids (groq/nim/...)."""
    enabled = sel.get("enabled")
    if enabled is not None and provider_id not in enabled:
        return False
    if provider_mode(provider_id, sel) == "custom":
        return model_id in ((sel.get("selected") or {}).get(provider_id) or [])
    return True
