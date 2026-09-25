"""Minimal read-only credential reader for the FastMCP peer app.

Reads the SAME MinIO+Fernet store the FastAPI BYOK system writes
(`llm/credentials.enc`, Fernet-encrypted with `KD_CREDS_KEY` env or
auto-generated `llm/kek.key`). On server startup we resolve any tool API
keys the user supplied via the Settings UI and inject them as os.environ
entries — so tools/<source>/service.py can keep reading
`os.environ.get(KEY_NAME)` unchanged.

Read-only: write paths live in apps/fastapi/domains/llm/credentials. This
peer app only consumes. KV → env injection happens ONCE at startup; key
changes require a fastmcp pod restart to take effect.

Callers use the dotted path — e.g.
`domains.settings.credentials.service.inject_user_keys_into_env(...)`.
"""
from __future__ import annotations
from . import domain, keys, params, service


__all__ = ["domain", "keys", "params", "service"]
