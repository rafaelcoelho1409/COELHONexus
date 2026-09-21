"""chat keys — settings-blob key + managed credential name (no behavior)."""
from __future__ import annotations


# Key of the chat endpoint blob inside the settings store
# (`CredentialStore.read_settings()["llm_endpoint"]` → {url, model}).
SETTINGS_KEY = "llm_endpoint"

# Managed-key name for this endpoint's API key (see credentials/keys.py).
KEY_ENV = "COELHO_LLM_API_KEY"
