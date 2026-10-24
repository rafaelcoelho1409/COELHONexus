"""minio domain — pure S3 client value-object builder (no I/O, deterministic)."""
from __future__ import annotations
from . import params

from botocore.config import Config


def build_boto_config(
    *, max_pool_connections: int | None, connect_timeout_s: float,
    read_timeout_s: float, max_attempts: int,
) -> Config:
    """S3 client config: signature v4, standard retry mode, and the pool size
    only when given (botocore's own default otherwise)."""
    kwargs: dict = {}
    if max_pool_connections is not None:
        kwargs["max_pool_connections"] = max_pool_connections
    return Config(
        signature_version = params.SIGNATURE_VERSION,
        connect_timeout = connect_timeout_s,
        read_timeout = read_timeout_s,
        retries = {"max_attempts": max_attempts, "mode": params.RETRY_MODE},
        **kwargs,
    )
