"""Capture exact tier responses under `dd/{slug}/ingestion/raw/{run_id}/{tier}/`."""
from __future__ import annotations
import domains, infra
from . import domain, entities, params

import asyncio
import uuid


async def _persist_capture_once(minio, capture: entities.Capture, key: str) -> entities.Capture:
    """Preserve the first capture timestamp on a task retry while checking the fetched file inventory stayed identical."""
    if await minio.exists(key):
        existing = domain.capture_from_json(await minio.read_text(key))
        if existing is None:
            raise RuntimeError(f"stored raw capture is unreadable at {key}")
        same = (
            existing.framework_slug == capture.framework_slug
            and existing.tier == capture.tier
            and existing.source_url == capture.source_url
            and existing.files == capture.files
            and existing.params == capture.params
            and existing.version == capture.version
        )
        if not same:
            raise RuntimeError(f"raw capture changed within an ingestion run at {key}")
        return existing
    await domains.dd.ingestion.storage.service.write_immutable(
        minio, key, domain.capture_json(capture), domains.dd.ingestion.storage.params.JSON_TYPE,
    )
    return capture


async def save(
    minio: infra.minio.service.MinIOStorage,
    capture: entities.Capture,
    texts: dict[str, str],
) -> None:
    """Write each file of `capture` (`texts`: {name: text}), then `capture.json` — last, so a capture whose files did not all arrive
    has no description and `load` finds none. The capture is stored in the run/tier-specific raw prefix."""
    keys = domains.dd.ingestion.storage.keys
    run_id = str((capture.params or {}).get("run_id") or "")
    if not run_id:
        run_id = uuid.uuid4().hex
        capture.params["run_id"] = run_id
    await asyncio.gather(*(
        domains.dd.ingestion.storage.service.write_immutable(
            minio, keys.raw_file_key(capture.framework_slug, f.name, run_id, capture.tier),
            texts[f.name], params.TEXT_TYPE,
        )
        for f in capture.files
    ))
    saved = await _persist_capture_once(
        minio, capture, keys.raw_capture_key(capture.framework_slug, run_id, capture.tier),
    )
    await minio.write(keys.raw_capture_key(capture.framework_slug), domain.capture_json(saved),
                      content_type=domains.dd.ingestion.storage.params.JSON_TYPE)


class Writer:
    """A tier's capture, written as the tier fetches: each file is stored the moment it arrives (a crawl of thousands of pages never holds
    them all), and `finish` writes `capture.json` last. Captures are immutable and partitioned by run and tier, including tiers that
    fall through, so retries cannot replace the original source bytes."""

    def __init__(
        self,
        minio: infra.minio.service.MinIOStorage,
        framework_slug: str,
        tier: str,
        source_url: str,
        *,
        run_id: str,
        capture_params: dict | None = None,
    ) -> None:
        self.minio = minio
        self.framework_slug = framework_slug
        self.tier = tier
        self.source_url = source_url
        self.run_id = run_id
        self.params: dict = dict(capture_params or {})
        self.files: list[entities.CapturedFile] = []
        self._raw_locks: dict[str, asyncio.Lock] = {}

    async def begin(self) -> None:
        # Runs own immutable raw prefixes. A tier downgrade can leave its capture for audit without touching the next tier.
        return None

    async def add(
        self,
        name: str,
        *,
        role: str,
        url: str,
        final_url: str = "",
        status: int = 0,
        content_type: str = "",
        text: str | None = None,
        data: bytes | None = None,
        error: str = "",
        for_url: str = "",
        via: str = "",
    ) -> entities.CapturedFile:
        """Keep one response (`text`, or `data` for bytes that are not text), or note one that gave nothing (`text` and `data` both None).
        HTML and XML are stored gzipped. Safe to call from concurrent tasks: a file's name comes from its URL, not from a counter."""
        keys = domains.dd.ingestion.storage.keys
        compress = text is not None and domain.should_compress(name)
        file = domain.captured_file(
            name = name, role = role, url = url, final_url = final_url, status = status, content_type = content_type,
            text = text if data is None else None, encoding = params.GZIP_ENCODING if compress else "", error = error, for_url = for_url, via = via,
        )
        if data is not None:
            file.stored, file.bytes = True, len(data)
            file.sha256 = domain.sha256_bytes(data)
            await self._write_raw_once(keys.raw_file_key(self.framework_slug, name, self.run_id, self.tier), data, params.BINARY_TYPE)
        elif file.stored:
            if compress:
                await self._write_raw_once(
                    keys.raw_file_key(self.framework_slug, domain.stored_name(file), self.run_id, self.tier),
                    domain.gzip_text(text), params.GZIP_TYPE,
                )
            else:
                await self._write_raw_once(
                    keys.raw_file_key(self.framework_slug, name, self.run_id, self.tier), text, params.TEXT_TYPE,
                )
        self.files.append(file)
        return file

    async def finish(self, extra_params: dict | None = None) -> entities.Capture:
        """Write the description, after every file: from here on the capture exists. Files are listed in name order (a crawl finishes in
        any order), so the same crawl describes itself the same way."""
        self.params.update(extra_params or {})
        self.params["run_id"] = self.run_id
        capture = domain.new_capture(
            self.framework_slug, self.tier, self.source_url, sorted(self.files, key = lambda f: f.name), capture_params = self.params,
        )
        saved = await _persist_capture_once(
            self.minio, capture,
            domains.dd.ingestion.storage.keys.raw_capture_key(self.framework_slug, self.run_id, self.tier),
        )
        await self.minio.write(
            domains.dd.ingestion.storage.keys.raw_capture_key(self.framework_slug), domain.capture_json(saved),
            content_type = domains.dd.ingestion.storage.params.JSON_TYPE,
        )
        return saved

    async def _write_raw_once(self, key: str, content: str | bytes, content_type: str) -> None:
        lock = self._raw_locks.setdefault(key, asyncio.Lock())
        async with lock:
            await domains.dd.ingestion.storage.service.write_immutable(self.minio, key, content, content_type)


async def load(minio: infra.minio.service.MinIOStorage, framework_slug: str) -> entities.Capture | None:
    """The framework's finished capture, or `None` (never captured, or one that did not finish)."""
    keys = domains.dd.ingestion.storage.keys
    try:
        raw = await minio.read_text(keys.raw_capture_key(framework_slug))
        capture = domain.capture_from_json(raw)
        return capture
    except Exception:  # noqa: BLE001 — a missing object and a MinIO hiccup both mean "no capture to read"
        return None


async def read_text(minio: infra.minio.service.MinIOStorage, capture: entities.Capture, name: str) -> str:
    """A file of the capture as text, `""` for one the capture does not have (or asked for and did not keep)."""
    file = domain.file_named(capture, name)
    if file is None or not file.stored:
        return ""
    run_id = str((capture.params or {}).get("run_id") or "")
    key = domains.dd.ingestion.storage.keys.raw_file_key(capture.framework_slug, domain.stored_name(file), run_id, capture.tier)
    if file.encoding == params.GZIP_ENCODING:
        return domain.gunzip_text(await minio.read_bytes(key))
    return await minio.read_text(key)


async def read_bytes(minio: infra.minio.service.MinIOStorage, capture: entities.Capture, name: str) -> bytes:
    """A file of the capture as bytes (Sphinx's `objects.inv`), `b""` for one the capture does not have."""
    file = domain.file_named(capture, name)
    if file is None or not file.stored:
        return b""
    run_id = str((capture.params or {}).get("run_id") or "")
    return await minio.read_bytes(domains.dd.ingestion.storage.keys.raw_file_key(capture.framework_slug, domain.stored_name(file), run_id, capture.tier))
