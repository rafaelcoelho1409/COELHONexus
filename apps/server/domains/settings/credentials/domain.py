from __future__ import annotations
from . import errors, keys

import json
from botocore.config import Config
from cryptography.fernet import Fernet, InvalidToken


def is_truthy(value: str | None) -> bool:
    """Env-flag parsing — the IMPORT_ENV_FLAG opt-in accepts common truthy spellings."""
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def build_boto_config(cfg) -> Config:
    """S3 client value-object from the frozen config group (takes config
    as an explicit param so it stays testable without config coupling)."""
    return Config(
        signature_version = cfg.signature_version,
        connect_timeout = cfg.connect_timeout_s,
        read_timeout = cfg.read_timeout_s,
        retries = {"max_attempts": cfg.max_attempts, "mode": cfg.retry_mode},
    )


def mask_key(key: str | None) -> str | None:
    s = (key or "").strip()
    if not s:
        return None
    return s[-4:] if len(s) >= 4 else "*" * len(s)


def validate_managed(key_env: str) -> None:
    if key_env not in keys.MANAGED_KEY_ENVS:
        raise errors.UnmanagedKeyEnv(f"unmanaged key_env: {key_env!r}")


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
    """Fallback re-encrypts ciphertext under the env KEK to avoid orphaning autogen-keyed credentials."""
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
