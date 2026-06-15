"""ycs/agents/byok — pure helpers: provider prefix + ChatLiteLLM build.

No I/O. Takes a plain dict (Pydantic-dumped `LLMConfig`) and returns a
LangChain `BaseChatModel` (single-model `ChatLiteLLM`) ready to drop in
wherever `app.state.llm` would go.

Why a separate single-model path instead of reusing `ChatLiteLLMRouter`
from the rotator: the rotator carries the full free-tier model catalog
+ FGTS-VA bandit + RetryPolicy + Redis cooldowns. The user's BYOK is
ONE model with ONE key — a cooler, simpler object. Mixing them would
mean the rotator's fallback fires through the user's key on every
quota hit, defeating the user's explicit choice.

2026-06-14 REWIRE: the BYOK form no longer carries `api_key` or
`base_url`. Provider keys are owned by the global Settings page (the
rotator credential store at `coelhonexus.llm.credentials.enc`); the
form just picks WHICH provider + model to use for THIS request and
the key is resolved here via `resolve_key(provider.key_env)`."""
from __future__ import annotations

from typing import Any

from langchain_litellm.chat_models import ChatLiteLLM

from domains.llm.credentials      import resolve_key
from domains.llm.rotator.discovery import PROVIDERS


# Discovery registry id → litellm provider prefix. The registry id is what
# the UI dropdown sends (`nim`, `groq`, ...); the prefix is what litellm
# needs in the `model` arg (`nvidia_nim/<id>`, `groq/<id>`, ...). Kept
# as data, not a lookup chain — the registry doesn't store this.
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

# litellm provider prefixes — the first segment of a fully-qualified
# model string. Used to tell apart already-prefixed ids (`groq/llama...`)
# from multi-segment vendor ids that need a prefix (`moonshotai/kimi...`
# from NIM, `mistralai/mixtral-...` from any host). Bare `model.contains("/")`
# is the wrong test — NIM / OpenRouter etc. all use vendor/model namespaces
# in their /v1/models entries.
_KNOWN_LITELLM_PREFIXES: frozenset[str] = frozenset(_PROVIDER_PREFIX_MAP.values())


def normalize_provider(name: str | None) -> str:
    """Map a registry id (or free-text user value) to the LiteLLM provider
    prefix used in `model="<prefix>/<model>"`."""
    if not name:
        return ""
    key = name.strip().lower()
    return _PROVIDER_PREFIX_MAP.get(key, key)


def build_byok_llm(config: dict[str, Any]) -> ChatLiteLLM | None:
    """Build a single-model `ChatLiteLLM` from a `LLMConfig`-shaped dict.

    Returns `None` when:
      - `provider` is unknown (not in the discovery registry)
      - the resolved `api_key` for that provider is empty (user hasn't
        configured a key on the Settings page)
      - `model` is missing

    Provider/model handling:
      - If `model` already contains "/", it is treated as fully qualified
        (e.g. `groq/llama-3.3-70b-versatile`) and used as-is.
      - Otherwise, the provider id is normalized to its LiteLLM prefix
        and prepended."""
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
