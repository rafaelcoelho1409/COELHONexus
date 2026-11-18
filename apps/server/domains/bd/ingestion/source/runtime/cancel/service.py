"""Extraction cancel flag — a Redis key per book that means "stop". The flag mechanics live in `infra.redis.service`; this binds them
to the book's cancel key and TTL (`progress/keys.py`, `progress/params.py`)."""
from __future__ import annotations
import infra
from .. import progress


async def request_cancel(r: infra.redis.service.Redis, book_id: str) -> None:
    await infra.redis.service.set_flag(
        r, progress.keys.cancel_key(book_id),
        progress.params.TTL_S,
        error_label = "[bd-cancel] set failed",
    )


async def is_cancelled(r: infra.redis.service.Redis, book_id: str) -> bool:
    return await infra.redis.service.flag_is_set(
        r, 
        progress.keys.cancel_key(book_id))


async def clear_cancel(r: infra.redis.service.Redis, book_id: str) -> None:
    await infra.redis.service.clear_flag(
        r, 
        progress.keys.cancel_key(book_id))
