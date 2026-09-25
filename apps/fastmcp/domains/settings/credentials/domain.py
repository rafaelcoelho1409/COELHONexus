"""Credential parsing — pure helpers (Functional Core).

Per docs/CODE-CONVENTIONS.md §4: no I/O, no clocks, no logging. Same
inputs → same outputs. MinIO fetching, KEK resolution, and the thread
backstop live in `service.py`; this module only turns (kek, bytes) into
a dict. Raises `InvalidToken` / `ValueError` on bad input — `service.py`
maps those to {} + warning.
"""
from __future__ import annotations

import json

from cryptography.fernet import Fernet


def decrypt_creds_dict(kek: bytes, raw: bytes) -> dict[str, str]:
    """Fernet-decrypt + parse + normalize a credentials blob."""
    data = Fernet(kek).decrypt(raw).decode("utf-8")
    loaded = json.loads(data)
    return {str(k): str(v) for k, v in (loaded or {}).items() if v}
