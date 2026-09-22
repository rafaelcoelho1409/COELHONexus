"""Sync (entry-builders can't await) MinIO-backed Fernet store; resolve_key() never raises — MinIO outage degrades to env-only."""
from __future__ import annotations
from . import config, domain, entities, keys, params

import copy
import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FutureTimeoutError
from typing import Optional

from botocore.exceptions import ClientError
from botocore.session import get_session
from cryptography.fernet import (
    Fernet,
    InvalidToken
)


logger = logging.getLogger(__name__)


class CredentialStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._cache: dict[str, str] = {}
        self._loaded_at: float = 0.0
        self._cache_valid: bool = False
        self._kek_bytes: Optional[bytes] = None
        self._settings_cache: Optional[dict] = None
        self._settings_loaded_at: float = 0.0
        self._endpoint = os.environ["MINIO_ENDPOINT"].strip()
        self._bucket = os.environ["MINIO_BUCKET_COELHONEXUS"]
        self._access_key = os.environ["AWS_ACCESS_KEY_ID"]
        self._secret_key = os.environ["AWS_SECRET_ACCESS_KEY"]
        self._boto_config = domain.build_boto_config(config.MINIO_CLIENT)

    def _client(self):
        return get_session().create_client(
            "s3",
            endpoint_url = self._endpoint,
            aws_access_key_id = self._access_key,
            aws_secret_access_key = self._secret_key,
            region_name = config.MINIO_CLIENT.region,
            config = self._boto_config,
        )

    def _get_object(self, key: str) -> Optional[bytes]:
        try:
            client = self._client()
            try:
                resp = client.get_object(
                    Bucket = self._bucket,
                    Key = key)
                return resp["Body"].read()
            finally:
                client.close()
        except ClientError as e:
            code = (e.response or {}).get("Error", {}).get("Code", "")
            if code in ("NoSuchKey", "404", "NoSuchBucket"):
                return None
            raise

    def _put_object(self, key: str, body: bytes, content_type: str) -> None:
        client = self._client()
        try:
            client.put_object(
                Bucket = self._bucket,
                Key = key,
                Body = body,
                ContentType = content_type,
            )
        finally:
            client.close()

    def _kek(self) -> bytes:
        if self._kek_bytes is not None:
            return self._kek_bytes
        if keys.KEK_ENV in os.environ:
            env = os.environ[keys.KEK_ENV].strip()
            if env:
                Fernet(env.encode())
                self._kek_bytes = env.encode()
                logger.info("[settings-creds] KEK from %s env", keys.KEK_ENV)
                return self._kek_bytes
        existing = self._get_object(config.STORAGE.kek)
        if existing and existing.strip():
            self._kek_bytes = existing.strip()
            return self._kek_bytes
        # Re-GET after PUT so concurrent first-boot processes converge.
        new = Fernet.generate_key()
        self._put_object(config.STORAGE.kek, new, "application/octet-stream")
        confirmed = self._get_object(config.STORAGE.kek)
        self._kek_bytes = (confirmed.strip() if confirmed else new)
        logger.info("[settings-creds] KEK auto-generated + persisted to %s", config.STORAGE.kek)
        return self._kek_bytes

    def _legacy_autogen_kek(self) -> Optional[bytes]:
        try:
            existing = self._get_object(config.STORAGE.kek)
            return existing.strip() if existing and existing.strip() else None
        except Exception:
            return None

    def _reload_locked(self) -> None:
        try:
            raw = self._get_object(config.STORAGE.credentials)
            if not raw:
                self._cache = {}
            else:
                loaded, used_fallback = domain.decrypt_credentials(
                    raw,
                    primary = self._kek(),
                    fallback = self._legacy_autogen_kek(),
                )
                self._cache = domain.normalize_credentials(loaded)
                if used_fallback:
                    try:
                        self._persist_locked()
                        logger.info("[settings-creds] migrated credentials.enc to env KEK")
                    except Exception as e:
                        logger.warning("[settings-creds] KEK migration re-encrypt failed: %s", e)
        except InvalidToken:
            logger.error("[settings-creds] credentials.enc failed to decrypt — env fallback")
            self._cache = {}
        except Exception as e:
            logger.warning(
                "[settings-creds] credential load failed (%s: %s) — env fallback",
                type(e).__name__, e,
            )
            # Don't wipe a previously-good cache on a transient blip.
            if not self._cache_valid:
                self._cache = {}
        self._loaded_at = time.monotonic()
        self._cache_valid = True

    def _ensure_fresh(self) -> None:
        now = time.monotonic()
        with self._lock:
            if self._cache_valid and (now - self._loaded_at) < params.CACHE_TTL_S:
                return
            self._reload_locked()

    def _persist_locked(self) -> None:
        blob = domain.encrypt_credentials(self._cache, self._kek())
        self._put_object(config.STORAGE.credentials, blob, "application/octet-stream")
        self._loaded_at = time.monotonic()
        self._cache_valid = True

    def _maybe_import_env_keys(self) -> int:
        if not domain.is_truthy(os.environ.get(params.IMPORT_ENV_FLAG)):
            return 0
        imported = 0
        with self._lock:
            self._reload_locked()
            changed = False
            for env_name in keys.MANAGED_KEY_ENVS:
                if self._cache.get(env_name):
                    continue
                if env_name not in os.environ:
                    continue
                val = os.environ[env_name].strip()
                if val:
                    self._cache[env_name] = val
                    changed = True
                    imported += 1
            if changed:
                self._persist_locked()
        if imported:
            logger.info("[settings-creds] imported %d env key(s) into the store", imported)
        return imported

    def _warm_blocking(self) -> None:
        with self._lock:
            self._kek()
            self._reload_locked()
        self._maybe_import_env_keys()
        logger.info("[settings-creds] warm: %d user key(s) loaded", len(self._cache))

    def warm(self) -> None:
        """Best-effort; never raises. Runs the blocking MinIO round trips in
        a throwaway thread with a hard wall-clock ceiling so a slow/unreachable
        MinIO can't stall the async lifespan that calls this."""
        executor = ThreadPoolExecutor(max_workers=1)
        try:
            future = executor.submit(self._warm_blocking)
            future.result(timeout=params.WARM_HARD_TIMEOUT_S)
        except _FutureTimeoutError:
            logger.warning(
                "[settings-creds] warm exceeded %ss hard timeout — env fallback active",
                params.WARM_HARD_TIMEOUT_S,
            )
        except Exception as e:
            logger.warning(
                "[settings-creds] warm failed (%s: %s) — env fallback active",
                type(e).__name__, e,
            )
        finally:
            executor.shutdown(wait=False)

    def resolve_key(self, key_env: str) -> str:
        """Never raises."""
        try:
            self._ensure_fresh()
            with self._lock:
                v = (self._cache.get(key_env) or "").strip()
            if v:
                return v
        except Exception as e:
            logger.debug("[settings-creds] resolve_key(%s) store miss: %s", key_env, e)
        if key_env not in os.environ:
            return ""
        return os.environ[key_env].strip()

    def set_key(self, key_env: str, api_key: str) -> entities.KeyStatus:
        domain.validate_managed(key_env)
        api_key = (api_key or "").strip()
        if not api_key:
            raise ValueError("empty api_key")
        with self._lock:
            self._reload_locked()
            self._cache[key_env] = api_key
            self._persist_locked()
        return self.key_status(key_env)

    def delete_key(self, key_env: str) -> entities.KeyStatus:
        domain.validate_managed(key_env)
        with self._lock:
            self._reload_locked()
            self._cache.pop(key_env, None)
            self._persist_locked()
        return self.key_status(key_env)

    def key_status(self, key_env: str) -> entities.KeyStatus:
        self._ensure_fresh()
        with self._lock:
            stored = (self._cache.get(key_env) or "").strip()
        if stored:
            return entities.KeyStatus(
                has_key = True,
                source = "user",
                last4 = domain.mask_key(stored))
        if key_env in os.environ:
            env = os.environ[key_env].strip()
            if env:
                return entities.KeyStatus(
                    has_key = True,
                    source = "env",
                    last4 = domain.mask_key(env))
        return entities.KeyStatus.unset()

    def read_settings(self, force: bool = False) -> dict:
        """`force=True` bypasses the TTL cache."""
        now = time.monotonic()
        if not force:
            with self._lock:
                if self._settings_cache is not None and \
                   (now - self._settings_loaded_at) < params.CACHE_TTL_S:
                    return copy.deepcopy(self._settings_cache)
        try:
            raw = self._get_object(config.STORAGE.settings)
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception as e:
            logger.warning("[settings-creds] settings read failed: %s", e)
            data = {}
        with self._lock:
            self._settings_cache = data
            self._settings_loaded_at = now
        return copy.deepcopy(data)

    def write_settings(self, settings: dict) -> None:
        self._put_object(
            config.STORAGE.settings,
            json.dumps(
                settings,
                separators = (",", ":")).encode("utf-8"),
            "application/json",
        )
        with self._lock:
            self._settings_cache = copy.deepcopy(settings)
            self._settings_loaded_at = time.monotonic()


_store: Optional[CredentialStore] = None
_store_lock = threading.Lock()


def get_store() -> CredentialStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = CredentialStore()
    return _store


def resolve_key(key_env: str) -> str:
    """Never raises."""
    try:
        return get_store().resolve_key(key_env)
    except Exception:
        if key_env not in os.environ:
            return ""
        return os.environ[key_env].strip()


def warm() -> None:
    try:
        get_store().warm()
    except Exception as e:
        logger.warning("[settings-creds] warm() failed: %s", e)
