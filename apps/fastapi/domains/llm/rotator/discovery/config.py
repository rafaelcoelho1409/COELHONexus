from __future__ import annotations

from .entities import FreeFilter, ProviderConfig


# `required=True` blocks DD runs when key is missing (NIM hosts mandatory embeddings + reranking).
PROVIDERS: dict[str, ProviderConfig] = {
    "groq": ProviderConfig(
        name           = "groq",
        url            = "https://api.groq.com/openai/v1/models",
        key_env        = "GROQ_API_KEY",
        auth_style     = "bearer",
        response_shape = "openai",
        free_filter    = FreeFilter.ALL,
    ),
    "nim": ProviderConfig(
        name           = "nim",
        url            = "https://integrate.api.nvidia.com/v1/models",
        key_env        = "NVIDIA_API_KEY",
        auth_style     = "bearer",
        response_shape = "openai",
        free_filter    = FreeFilter.ALL,
        required       = True,
    ),
    "cerebras": ProviderConfig(
        name           = "cerebras",
        url            = "https://api.cerebras.ai/v1/models",
        key_env        = "CEREBRAS_API_KEY",
        auth_style     = "bearer",
        response_shape = "openai",
        free_filter    = FreeFilter.ALL,
    ),
    "mistral": ProviderConfig(
        name           = "mistral",
        url            = "https://api.mistral.ai/v1/models",
        key_env        = "MISTRAL_API_KEY",
        auth_style     = "bearer",
        response_shape = "openai",
        free_filter    = FreeFilter.MISTRAL,
    ),
    "gemini": ProviderConfig(
        name           = "gemini",
        url            = "https://generativelanguage.googleapis.com/v1beta/models",
        key_env        = "GOOGLE_API_KEY",
        auth_style     = "query-key",
        response_shape = "gemini",
        free_filter    = FreeFilter.GEMINI,
    ),
    "sambanova": ProviderConfig(
        name           = "sambanova",
        url            = "https://api.sambanova.ai/v1/models",
        key_env        = "SAMBANOVA_API_KEY",
        auth_style     = "bearer",
        response_shape = "openai",
        free_filter    = FreeFilter.SAMBANOVA_PRICING,
        enabled        = False,
    ),
    "deepseek": ProviderConfig(
        name           = "deepseek",
        url            = "https://api.deepseek.com/v1/models",
        key_env        = "DEEPSEEK_API_KEY",
        auth_style     = "bearer",
        response_shape = "openai",
        free_filter    = FreeFilter.ALWAYS_FALSE,
        enabled        = False,    # direct API paid-only; NIM-hosted is the free path
    ),
}
