from __future__ import annotations

import json
from cryptography.fernet import Fernet, InvalidToken

from .errors import UnmanagedKeyEnv
from .keys import MANAGED_KEY_ENVS


def mask_key(key: str | None) -> str | None:
    s = (key or "").strip()
    if not s:
        return None
    return s[-4:] if len(s) >= 4 else "*" * len(s)


def validate_managed(key_env: str) -> None:
    if key_env not in MANAGED_KEY_ENVS:
        raise UnmanagedKeyEnv(f"unmanaged key_env: {key_env!r}")


def encrypt_credentials(creds: dict[str, str], kek: bytes) -> bytes:
    plaintext = json.dumps(
        creds, 
        separators = (",", ":")).encode("utf-8")
    return Fernet(kek).encrypt(plaintext)


def decrypt_credentials(
    raw: bytes,
    primary: bytes,
    fallback: bytes | None,
) -> tuple[dict[str, str], bool]:
    """(mapping, used_fallback). Fallback migrates ciphertext from the
    autogen KEK to a newly-introduced env KEK without orphaning saved keys."""
    try:
        data = Fernet(primary).decrypt(raw).decode("utf-8")
        return json.loads(data), False
    except InvalidToken:
        if fallback and fallback != primary:
            data = Fernet(fallback).decrypt(raw).decode("utf-8")
            return json.loads(data), True
        raise


def normalize_credentials(loaded: dict) -> dict[str, str]:
    return {str(k): str(v) for k, v in (loaded or {}).items() if v}
