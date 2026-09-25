"""Credential-store identifiers — per docs/CODE-CONVENTIONS.md §2.

Key shapes agreed with the FastAPI writer (`apps/fastapi/domains/settings/`):
both sides must name the same MinIO objects and env var or the store
silently forks. Boto/deployment env names (MINIO_ENDPOINT, AWS_* keys)
stay inline in `service.py` — standard SDK reads, not cross-app contract.
"""
from __future__ import annotations


# Encrypted credentials blob the Settings UI writes, this peer app reads.
CREDENTIALS_KEY: str = "llm/credentials.enc"

# Auto-generated KEK blob (fallback when the operator-managed env var below
# is unset).
KEK_KEY: str = "llm/kek.key"

# Operator-managed KEK env var (preferred over the MinIO blob).
KEK_ENV: str = "KD_CREDS_KEY"
