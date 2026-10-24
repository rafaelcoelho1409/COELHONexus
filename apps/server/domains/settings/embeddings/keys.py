"""embeddings keys — settings-blob key + managed credential name (no behavior)."""
from __future__ import annotations


# Key of the embedding endpoint blob inside the settings store
# (`CredentialStore.read_settings()["embedding_endpoint"]` → {url, model}).
SETTINGS_KEY = "embedding_endpoint"

# Managed-key name for this endpoint's API key (see credentials/keys.py).
KEY_ENV = "COELHO_EMBEDDING_API_KEY"
