"""ycs/agents/byok — provider prefix + single-model ChatLiteLLM build.
Single-model (not ChatLiteLLMRouter) — rotator fallback through user's key on quota hits defeats explicit provider choice.
Keys resolved from global credential store, not the BYOK form."""
from __future__ import annotations

from typing import Any

from langchain_litellm.chat_models import ChatLiteLLM

from domains.llm.credentials      import resolve_key
from domains.llm.rotator.discovery import PROVIDERS


# Registry id → litellm prefix. Registry doesn't store prefix, so kept as data here.
_PROVIDER_PREFIX_MAP: dict[str, str] = {
    "nim":       "nvidia_nim",
    "groq":      "groq",
    "cerebras":  "cerebras",
    "gemini":    "gemini",
    "mistral":   "mistral",
    "deepseek":  "deepseek",
    "sambanova": "sambanova",
    "openai":     "openai",
    "anthropic":  "anthropic",
    "openrouter": "openrouter",
    "ollama":     "ollama",
}

# litellm provider prefixes. Bare `model.contains("/")` is wrong — NIM/OpenRouter use vendor/model
# namespaces in their /v1/models entries, so "/" alone can't distinguish already-prefixed from bare ids.
_KNOWN_LITELLM_PREFIXES: frozenset[str] = frozenset(_PROVIDER_PREFIX_MAP.values())


def normalize_provider(name: str | None) -> str:
    """Map a registry id to the LiteLLM provider prefix used in `model="<prefix>/<model>"`."""
    if not name:
        return ""
    key = name.strip().lower()
    return _PROVIDER_PREFIX_MAP.get(key, key)


def build_byok_llm(config: dict[str, Any]) -> ChatLiteLLM | None:
    """Build a single-model `ChatLiteLLM` from a `LLMConfig` dict. Returns `None` if provider unknown, key unset, or model missing."""
    model = config.get("model")
    provider_id = (config.get("provider") or "").strip().lower()
    if not model or not provider_id:
        return None
    cfg = PROVIDERS.get(provider_id)
    if cfg is None:
        return None
    api_key = resolve_key(cfg.key_env)
    if not api_key:
        return None
    full_model = model.strip()
    head = full_model.partition("/")[0]
    if head not in _KNOWN_LITELLM_PREFIXES:
        prefix = normalize_provider(provider_id)
        if prefix:
            full_model = f"{prefix}/{full_model}"
    kwargs: dict[str, Any] = {
        "model":   full_model,
        "api_key": api_key,
    }
    temperature = config.get("temperature")
    if temperature is not None:
        kwargs["temperature"] = float(temperature)
    return ChatLiteLLM(**kwargs)
