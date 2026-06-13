"""ycs/agents/byok — pure helpers: provider prefix + ChatLiteLLM build.

No I/O. Takes a plain dict (Pydantic-dumped `LLMConfig`) and returns a
LangChain `BaseChatModel` (single-model `ChatLiteLLM`) ready to drop in
wherever `app.state.llm` would go.

Why a separate single-model path instead of reusing `ChatLiteLLMRouter`
from the rotator: the rotator carries the full free-tier model catalog
+ FGTS-VA bandit + RetryPolicy + Redis cooldowns. The user's BYOK is
ONE model with ONE key — a cooler, simpler object. Mixing them would
mean the rotator's fallback fires through the user's key on every
quota hit, defeating the user's explicit choice."""
from __future__ import annotations

from typing import Any

from langchain_litellm.chat_models import ChatLiteLLM


_PROVIDER_PREFIX_MAP: dict[str, str] = {
    "nvidia":     "nvidia_nim",
    "nim":        "nvidia_nim",
    "groq":       "groq",
    "cerebras":   "cerebras",
    "google":     "gemini",
    "gemini":     "gemini",
    "openai":     "openai",
    "anthropic":  "anthropic",
    "mistral":    "mistral",
    "deepseek":   "deepseek",
    "openrouter": "openrouter",
    "ollama":     "ollama",
}


def normalize_provider(name: str | None) -> str:
    """Map the user-facing provider name (Form value, free text) to the
    LiteLLM provider prefix used in `model="<prefix>/<model>"`."""
    if not name:
        return ""
    key = name.strip().lower()
    return _PROVIDER_PREFIX_MAP.get(key, key)


def build_byok_llm(config: dict[str, Any]) -> ChatLiteLLM | None:
    """Build a single-model `ChatLiteLLM` from a `LLMConfig`-shaped dict.

    Returns `None` if the config is incomplete (missing `model` or
    `api_key`). Caller falls back to the rotator chain in that case.

    Provider/model handling:
      - If `model` already contains "/", it is treated as fully qualified
        (e.g. `groq/llama-3.3-70b-versatile`) and used as-is.
      - Otherwise, `provider` is normalized via `normalize_provider()`
        and prepended."""
    model = config.get("model")
    api_key = config.get("api_key")
    if not model or not api_key:
        return None
    full_model = model.strip()
    if "/" not in full_model:
        prefix = normalize_provider(config.get("provider"))
        if prefix:
            full_model = f"{prefix}/{full_model}"
    kwargs: dict[str, Any] = {
        "model":   full_model,
        "api_key": api_key,
    }
    temperature = config.get("temperature")
    if temperature is not None:
        kwargs["temperature"] = float(temperature)
    base_url = config.get("base_url")
    if base_url:
        kwargs["api_base"] = base_url
    return ChatLiteLLM(**kwargs)
