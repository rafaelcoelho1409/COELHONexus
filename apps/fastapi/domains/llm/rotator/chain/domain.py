from __future__ import annotations

from .keys import (
    DD_SYNTH_WRITE_HEAVYWEIGHTS,
    _LITELLM_PREFIX_TO_PROVIDER,
    _NON_CHAT_MARKERS,
    _PROVIDER_KEY_ENV,
)
from .patterns import (
    MOE_RE,
    PARAM_SIZE_RE,
    _EOL_PHRASES,
)


def classify_error(exc: Exception) -> str:
    """Map a litellm/httpx exception to ParetoBandit's error_class taxonomy."""
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


def is_eol_error(exc: Exception) -> bool:
    """True if exception text indicates EOL/deprecated/decommissioned —
    catalog must drop NOW, not after cooldown."""
    msg  = str(exc).lower()
    name = type(exc).__name__.lower()
    if "notfound" in name:
        return True
    if "410" in msg or " gone" in msg:
        return True
    if "404" in msg and ("model" in msg or "function" in msg):
        return True
    return any(p in msg for p in _EOL_PHRASES)


def is_heavyweight(deployment_id: str) -> bool:
    """True if `deployment_id` is on the SAWC-writer heavyweight whitelist."""
    return any(s in deployment_id for s in DD_SYNTH_WRITE_HEAVYWEIGHTS)


def is_non_chat_model(model_id: str) -> bool:
    """True for non-chat models (embedders, rerankers, vision, ASR, classifiers)."""
    name = (model_id or "").lower()
    return any(m in name for m in _NON_CHAT_MARKERS)


def passes_capability_floor(model_id: str, min_b: float) -> bool:
    """True if model meets the param-size floor. MoE bypasses. No parseable
    size → True (newer-named frontier)."""
    if min_b <= 0:
        return True
    name = (model_id or "").lower()
    if MOE_RE.search(name):
        return True
    sizes = [float(x) for x in PARAM_SIZE_RE.findall(name)]
    if sizes:
        return max(sizes) >= min_b
    return True


def provider_key_env(provider: str) -> str:
    """LiteLLM prefix → env-var name. Falls back to NVIDIA_API_KEY."""
    return _PROVIDER_KEY_ENV.get(provider, "NVIDIA_API_KEY")


def entry_provider_and_model(entry: dict) -> tuple[str, str]:
    """(registry_provider_id, model_id) from a LiteLLM entry."""
    m = (entry.get("litellm_params") or {}).get("model", "")
    prefix, _, model = m.partition("/")
    return _LITELLM_PREFIX_TO_PROVIDER.get(prefix, prefix), model


def provider_mode(provider_id: str, sel: dict) -> str:
    """'all' (every free model, opt-in new) or 'custom' (only selected)."""
    return (sel.get("mode") or {}).get(provider_id, "all")


def selection_allows(provider_id: str, model_id: str, sel: dict) -> bool:
    """Canonical BYOK predicate — shared by entry filter AND discovery path.
    Provider ids are REGISTRY ids (groq/nim/...)."""
    enabled = sel.get("enabled")
    if enabled is not None and provider_id not in enabled:
        return False
    if provider_mode(provider_id, sel) == "custom":
        return model_id in ((sel.get("selected") or {}).get(provider_id) or [])
    return True
