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
"""
from .service import inject_user_keys_into_env, resolve_key


__all__ = ["inject_user_keys_into_env", "resolve_key"]
