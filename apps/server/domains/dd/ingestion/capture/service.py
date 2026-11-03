"""Capture — storage: write what a tier fetched under `ingestion/raw/{slug}/`, read it back for a transform."""
from __future__ import annotations
import domains, infra
from . import domain, entities, params

import asyncio


async def save(
    minio: infra.minio.service.MinIOStorage,
    capture: entities.Capture,
    texts: dict[str, str],
) -> None:
    """Write each file of `capture` (`texts`: {name: text}), then `capture.json` — last, so a capture whose files did not all arrive
    has no description and `load` finds none. A framework's older capture is set aside before a run starts
    (`storage.service.stash_old_copy`), so there is nothing to overwrite here."""
    keys = domains.dd.ingestion.storage.keys
    await asyncio.gather(*(
        minio.write(keys.raw_file_key(capture.framework_slug, f.name), texts[f.name], content_type = params.TEXT_TYPE)
        for f in capture.files
    ))
    await minio.write(
        keys.raw_capture_key(capture.framework_slug), domain.capture_json(capture),
        content_type = domains.dd.ingestion.storage.params.JSON_TYPE,
    )


class Writer:
    """A tier's capture, written as the tier fetches: each file is stored the moment it arrives (a crawl of thousands of pages never holds
    them all), and `finish` writes `capture.json` last. The tier that ends up serving a framework is the only one whose capture is
    left: `begin` clears whatever an earlier try of this run (a tier that fell through) left under the framework's raw prefix."""

    def __init__(
        self,
        minio: infra.minio.service.MinIOStorage,
        framework_slug: str,
        tier: str,
        source_url: str,
        *,
        capture_params: dict | None = None,
    ) -> None:
        self.minio = minio
        self.framework_slug = framework_slug
        self.tier = tier
        self.source_url = source_url
        self.params: dict = dict(capture_params or {})
        self.files: list[entities.CapturedFile] = []

    async def begin(self) -> None:
        await self.minio.delete_prefix(domains.dd.ingestion.storage.keys.raw_prefix(self.framework_slug))

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
            await self.minio.write(keys.raw_file_key(self.framework_slug, name), data, content_type = params.BINARY_TYPE)
        elif file.stored:
            if compress:
                await self.minio.write(
                    keys.raw_file_key(self.framework_slug, domain.stored_name(file)), domain.gzip_text(text), content_type = params.GZIP_TYPE,
                )
            else:
                await self.minio.write(keys.raw_file_key(self.framework_slug, name), text, content_type = params.TEXT_TYPE)
        self.files.append(file)
        return file

    async def finish(self, extra_params: dict | None = None) -> entities.Capture:
        """Write the description, after every file: from here on the capture exists. Files are listed in name order (a crawl finishes in
        any order), so the same crawl describes itself the same way."""
        self.params.update(extra_params or {})
        capture = domain.new_capture(
            self.framework_slug, self.tier, self.source_url, sorted(self.files, key = lambda f: f.name), capture_params = self.params,
        )
        await self.minio.write(
            domains.dd.ingestion.storage.keys.raw_capture_key(self.framework_slug), domain.capture_json(capture),
            content_type = domains.dd.ingestion.storage.params.JSON_TYPE,
        )
        return capture


async def load(minio: infra.minio.service.MinIOStorage, framework_slug: str) -> entities.Capture | None:
    """The framework's finished capture, or `None` (never captured, or one that did not finish)."""
    keys = domains.dd.ingestion.storage.keys
    try:
        return domain.capture_from_json(await minio.read_text(keys.raw_capture_key(framework_slug)))
    except Exception:  # noqa: BLE001 — a missing object and a MinIO hiccup both mean "no capture to read"
        return None


async def read_text(minio: infra.minio.service.MinIOStorage, capture: entities.Capture, name: str) -> str:
    """A file of the capture as text, `""` for one the capture does not have (or asked for and did not keep)."""
    file = domain.file_named(capture, name)
    if file is None or not file.stored:
        return ""
    key = domains.dd.ingestion.storage.keys.raw_file_key(capture.framework_slug, domain.stored_name(file))
    if file.encoding == params.GZIP_ENCODING:
        return domain.gunzip_text(await minio.read_bytes(key))
    return await minio.read_text(key)


async def read_bytes(minio: infra.minio.service.MinIOStorage, capture: entities.Capture, name: str) -> bytes:
    """A file of the capture as bytes (Sphinx's `objects.inv`), `b""` for one the capture does not have."""
    file = domain.file_named(capture, name)
    if file is None or not file.stored:
        return b""
    return await minio.read_bytes(domains.dd.ingestion.storage.keys.raw_file_key(capture.framework_slug, domain.stored_name(file)))
