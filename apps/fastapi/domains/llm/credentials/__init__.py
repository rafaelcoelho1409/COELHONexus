"""BYOK credential store for the LLM rotator.

Replaces "API keys via Helm configmap (env)" as the *only* key source with a
user-managed, encrypted-at-rest store the settings UI writes to. The rotator
resolves a provider key via `resolve_key(key_env)`:

    user-store key (Fernet-encrypted MinIO blob)  →  env var (Helm fallback)

so nothing breaks during/after migration — env keys keep working until the
user overrides them through the UI. See
docs/LLM-ROTATOR-SETTINGS-SOTA-2026-05-31.md.

Public surface:
    resolve_key(key_env)         — hot-path read (sync, never raises)
    get_store()                  — CredentialStore singleton (API/UI path)
    warm()                       — eager KEK init + cache load (lifespan/worker)
"""
from __future__ import annotations

from .service import (
    CredentialStore,
    get_store,
    resolve_key,
    warm,
)

__all__ = [
    "CredentialStore",
    "get_store",
    "resolve_key",
    "warm",
]
