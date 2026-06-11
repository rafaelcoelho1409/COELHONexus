"""Sync MinIO+Fernet credential reader — fastmcp peer app.

Subset port of apps/fastapi/domains/llm/credentials/service.py — READ ONLY.
Same MinIO endpoint, same KEK resolution, same `llm/credentials.enc`
ciphertext file. Decrypts to a `{env_var_name: api_key}` dict; the public
helpers resolve a single key OR inject all known tool keys into os.environ.

Why subset:
  - fastmcp doesn't write keys (the Settings UI in apps/fasthtml writes via
    apps/fastapi/api/v1/rr/tool-credentials/).
  - No TTL cache / settings storage / hot-reload needed — fastmcp resolves
    once at startup; key changes require a pod restart.

If any expected env var is unset (MINIO_ENDPOINT, KEK, etc.) or the read
fails for any reason, all functions degrade gracefully — env-only mode,
log a warning, never raise.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

from botocore.config import Config
from botocore.exceptions import ClientError
from botocore.session import get_session
from cryptography.fernet import Fernet, InvalidToken


logger = logging.getLogger(__name__)


# Same MinIO paths the fastapi credential store writes to.
_CREDENTIALS_KEY = "llm/credentials.enc"
_KEK_KEY = "llm/kek.key"
_KEK_ENV = "KD_CREDS_KEY"


def _minio_client():
    """Build a sync S3 client. Reads the same env vars fastapi's store uses."""
    endpoint = os.environ.get("MINIO_ENDPOINT", "").strip()
    if not endpoint:
        raise RuntimeError("MINIO_ENDPOINT unset — cannot read credentials store")
    return get_session().create_client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID", ""),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY", ""),
        region_name="us-east-1",
        config=Config(
            signature_version="s3v4",
            connect_timeout=10,
            read_timeout=30,
            retries={"max_attempts": 5, "mode": "standard"},
        ),
    )


def _get_object(key: str) -> Optional[bytes]:
    """Best-effort sync MinIO GET. Returns None for missing object; raises
    only on connection/credential failures (caught by callers)."""
    bucket = os.environ.get("MINIO_BUCKET_COELHONEXUS", "")
    if not bucket:
        raise RuntimeError("MINIO_BUCKET_COELHONEXUS unset")
    client = _minio_client()
    try:
        try:
            resp = client.get_object(Bucket=bucket, Key=key)
            return resp["Body"].read()
        except ClientError as e:
            code = (e.response or {}).get("Error", {}).get("Code", "")
            if code in ("NoSuchKey", "404", "NoSuchBucket"):
                return None
            raise
    finally:
        client.close()


def _resolve_kek() -> Optional[bytes]:
    """KEK from env (preferred — operator-managed) or from the MinIO autogen
    blob. None if neither yields a usable key."""
    env = os.environ.get(_KEK_ENV, "").strip()
    if env:
        try:
            Fernet(env.encode())   # validate
            return env.encode()
        except Exception as e:
            logger.warning("[creds] %s in env is not a valid Fernet key: %s", _KEK_ENV, e)
    try:
        raw = _get_object(_KEK_KEY)
        return raw.strip() if raw and raw.strip() else None
    except Exception as e:
        logger.debug("[creds] KEK fetch from MinIO failed: %s", e)
        return None


def _load_creds_dict() -> dict[str, str]:
    """Decrypt + parse the credentials file. {} on any failure."""
    try:
        kek = _resolve_kek()
        if not kek:
            return {}
        raw = _get_object(_CREDENTIALS_KEY)
        if not raw:
            return {}
        data = Fernet(kek).decrypt(raw).decode("utf-8")
        loaded = json.loads(data)
        return {str(k): str(v) for k, v in (loaded or {}).items() if v}
    except InvalidToken:
        logger.warning("[creds] %s failed to decrypt — env fallback", _CREDENTIALS_KEY)
        return {}
    except Exception as e:
        logger.warning(
            "[creds] credential load failed (%s: %s) — env fallback",
            type(e).__name__, e,
        )
        return {}


def resolve_key(key_env: str) -> str:
    """User-stored key (MinIO) if present; else env; else empty string."""
    stored = _load_creds_dict().get(key_env, "").strip()
    if stored:
        return stored
    return os.environ.get(key_env, "").strip()


def inject_user_keys_into_env(key_envs: tuple[str, ...]) -> int:
    """At server startup: for each env-var name in `key_envs`, if the user
    supplied a value via the Settings UI, set it in os.environ so tool code
    that reads `os.environ.get(name)` picks it up. Env-set values are NOT
    overwritten — user-stored takes priority only when env is empty.

    Returns the count of keys injected (useful for a startup log line)."""
    creds = _load_creds_dict()
    injected = 0
    for name in key_envs:
        stored = creds.get(name, "").strip()
        if not stored:
            continue
        existing = os.environ.get(name, "").strip()
        if existing == stored:
            continue   # already correct in env
        os.environ[name] = stored
        injected += 1
        logger.info("[creds] injected user-supplied %s into env (last4=…%s)",
                    name, stored[-4:] if len(stored) >= 4 else "***")
    return injected
