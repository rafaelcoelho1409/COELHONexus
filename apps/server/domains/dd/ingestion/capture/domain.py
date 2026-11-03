"""Capture — pure helpers: how a fetched file and a capture are described, named and compressed, and how the description is stored."""
from __future__ import annotations
import domains
from . import entities, params

import gzip
import hashlib
import json
import time
from dataclasses import asdict, fields
from urllib.parse import urlparse


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def captured_file(
    *,
    name: str,
    role: str,
    url: str,
    final_url: str,
    status: int,
    content_type: str,
    text: str | None,
    encoding: str = "",
    error: str = "",
    for_url: str = "",
    via: str = "",
) -> entities.CapturedFile:
    """The description of a fetched file (`text` is `None` for one that was asked for and not kept: a 404, a failed request)."""
    kept = text is not None
    return entities.CapturedFile(
        name = name, role = role, url = url, final_url = final_url or url, status = status,
        content_type = content_type or "", sha256 = sha256_text(text) if kept else "", bytes = len(text.encode("utf-8")) if kept else 0,
        stored = kept, encoding = encoding if kept else "", error = error, for_url = for_url, via = via,
    )


def extension_for(url: str, content_type: str = "") -> str:
    """The extension a response is kept under: by what the server says it is, else by how its URL ends."""
    kind = (content_type or "").split(";", 1)[0].strip().lower()
    for needle, extension in params.EXTENSION_BY_TYPE:
        if needle in kind:
            return extension
    # `text/plain` says little (raw.githubusercontent.com serves every file that way): the URL's own ending wins over it.
    path = urlparse(url).path.lower()
    return next((suffix for suffix in params.EXTENSION_BY_SUFFIX if path.endswith(suffix)), params.DEFAULT_EXTENSION)


def page_file_name(url: str, content_type: str = "") -> str:
    """A page's name inside the capture: from its URL alone, so it is the same however the crawl's requests were interleaved."""
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[: params.URL_HASH_CHARS]
    return f"{params.PAGE_NAME_PREFIX}{digest}{extension_for(url, content_type)}"


def should_compress(name: str) -> bool:
    return name.lower().endswith(params.COMPRESS_EXTENSIONS)


def gzip_text(text: str) -> bytes:
    return gzip.compress(text.encode("utf-8"), compresslevel = params.GZIP_LEVEL, mtime = 0)


def gunzip_text(data: bytes) -> str:
    return gzip.decompress(data).decode("utf-8")


def stored_name(file: entities.CapturedFile) -> str:
    """The name the object of `file` has under `files/`."""
    return file.name + params.GZIP_SUFFIX if file.encoding == params.GZIP_ENCODING else file.name


def new_capture(
    framework_slug: str,
    tier: str,
    source_url: str,
    files: list[entities.CapturedFile],
    *,
    capture_params: dict | None = None,
    captured_at: float | None = None,
) -> entities.Capture:
    return entities.Capture(
        framework_slug = framework_slug, tier = tier, source_url = source_url,
        captured_at = time.time() if captured_at is None else captured_at,
        files = files, params = dict(capture_params or {}), version = params.SCHEMA_VERSION,
    )


def capture_json(capture: entities.Capture) -> str:
    return json.dumps(asdict(capture), ensure_ascii = False, indent = 1)


def capture_from_json(raw: str | bytes | None) -> entities.Capture | None:
    """The capture a `capture.json` describes; `None` when there is none, it cannot be read, or it comes from a newer schema."""
    stored = domains.dd.ingestion.storage.domain.parse_json(raw, None)
    if not isinstance(stored, dict) or stored.get("version", 1) > params.SCHEMA_VERSION:
        return None
    known = {f.name for f in fields(entities.CapturedFile)}
    files = [entities.CapturedFile(**{k: v for k, v in f.items() if k in known}) for f in stored.get("files") or []]
    return entities.Capture(
        framework_slug = stored.get("framework_slug", ""), tier = stored.get("tier", ""), source_url = stored.get("source_url", ""),
        captured_at = stored.get("captured_at", 0.0), files = files, params = stored.get("params") or {},
        version = stored.get("version", 1),
    )


def file_named(capture: entities.Capture, name: str) -> entities.CapturedFile | None:
    return next((f for f in capture.files if f.name == name), None)


def files_with_role(capture: entities.Capture, role: str) -> list[entities.CapturedFile]:
    return [f for f in capture.files if f.role == role]


def file_for_url(capture: entities.Capture, url: str) -> entities.CapturedFile | None:
    """The kept response that stands for the page `url`: its own, or its markdown twin's."""
    return next((f for f in capture.files if f.stored and f.role == params.ROLE_PAGE and (f.for_url or f.url) == url), None)
