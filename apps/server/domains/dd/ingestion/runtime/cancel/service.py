"""Ingestion cancel flag — a Redis key per run that means "stop". The flag mechanics live in `infra.redis.service`; this binds them to the ingestion run's cancel key and TTL (`progress/keys.py`, `progress/params.py`)."""
from __future__ import annotations
import domains, infra


async def request_cancel(r: infra.redis.service.Redis, run_id: str) -> None:
    await infra.redis.service.set_flag(
        r, domains.dd.ingestion.runtime.progress.keys.cancel_key(run_id),
        domains.dd.ingestion.runtime.progress.params.TTL_S,
        error_label = "[cancel] set failed",
    )


async def is_cancelled(r: infra.redis.service.Redis, run_id: str) -> bool:
    return await infra.redis.service.flag_is_set(
        r, domains.dd.ingestion.runtime.progress.keys.cancel_key(run_id),
    )


async def clear_cancel(r: infra.redis.service.Redis, run_id: str) -> None:
    await infra.redis.service.clear_flag(
        r, domains.dd.ingestion.runtime.progress.keys.cancel_key(run_id),
    )
