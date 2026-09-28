"""chat keys — settings-blob key + managed credential name (no behavior)."""
from __future__ import annotations


# Key of the chat endpoint blob inside the settings store
# (`CredentialStore.read_settings()["llm_endpoint"]` → {url, model}).
SETTINGS_KEY = "llm_endpoint"

# Managed-key names for this endpoint's API key (see credentials/keys.py).
# `LLM_API_KEY` is the current name; `COELHO_LLM_API_KEY` is the legacy
# name, still honored for deployed envs / already-stored keys.
KEY_ENVS: tuple[str, ...] = ("LLM_API_KEY", "COELHO_LLM_API_KEY")
