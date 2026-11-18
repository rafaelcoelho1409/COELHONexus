"""Pure helpers (no I/O) — a book's identity and its manifest, the one JSON that says what has been extracted."""
from __future__ import annotations
from . import params, patterns

import hashlib
import io
import json
import zipfile


def book_id(data: bytes) -> str:
    """A book's id is its content: the same file uploaded twice is the same book, and the second upload finds it already extracted."""
    return hashlib.sha256(data).hexdigest()[:16]


def looks_like_pdf(data: bytes) -> bool:
    """Whether the bytes start like a PDF (the extractor decides if it can actually read it)."""
    return data[:1024].lstrip().startswith(params.PDF_MAGIC)


def looks_like_epub(data: bytes) -> bool:
    """Whether the ZIP contains EPUB's required media-type marker and container directory."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if len(archive.filelist) > 1000:
                return False
            info = archive.getinfo("mimetype")
            return (
                info.file_size <= 64
                and "META-INF/container.xml" in archive.NameToInfo
                and archive.read(info) == params.EPUB_MIMETYPE
            )
    except (EOFError, KeyError, OSError, RuntimeError, zipfile.BadZipFile):
        return False


def detect_format(data: bytes) -> str | None:
    if looks_like_pdf(data):
        return "pdf"
    if looks_like_epub(data):
        return "epub"
    return None


def format_from_filename(filename: str) -> str | None:
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return suffix if suffix in ("pdf", "epub") else None


def book_title(filename: str, metadata_title: str) -> str:
    """The title the source gives itself, else its file name without the extension and the underscores."""
    title = " ".join((metadata_title or "").split())
    if title:
        return title
    return patterns.FILENAME_NOISE_RE.sub(" ", patterns.FILENAME_SUFFIX_RE.sub("", filename or "")).strip() or "Untitled book"


def parse_json(raw: str | bytes | None, default):
    """What a stored value says, or `default` when there is none or it cannot be read."""
    if not raw:
        return default
    try:
        return json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
    except ValueError:  # malformed JSON and undecodable bytes are both ValueErrors
        return default


def new_manifest(*, book_id: str, filename: str, size_bytes: int, now: float) -> dict:
    """A book that has just been uploaded: nothing is known of its content units until the worker opens the file."""
    return {
        "book_id":    book_id,
        "title":      book_title(filename, ""),
        "filename":   filename,
        "size_bytes": int(size_bytes),
        "n_pages":    0,
        "status":     params.STATUS_QUEUED,
        "error":      None,
        "outline":    [],
        "chapters":   [],
        "pages":      [],
        "extraction_id": None,
        "created_at": now,
        "updated_at": now,
    }


def with_info(manifest: dict, *, title: str, n_pages: int, outline: list[dict], chapters: list[dict], now: float) -> dict:
    """The manifest once the file has been opened: its page count, title and outline."""
    return {
        **manifest,
        "title":      book_title(manifest.get("filename", ""), title),
        "n_pages":    int(n_pages),
        "outline":    outline,
        "chapters":   chapters,
        "updated_at": now,
    }


def with_status(manifest: dict, status: str, now: float, error: str | None = None) -> dict:
    return {**manifest, "status": status, "error": error, "updated_at": now}


def with_extraction(manifest: dict, extraction_id: str, now: float, *, reset: bool = False) -> dict:
    """Select the current immutable extraction generation, optionally starting it empty."""
    updated = {**manifest, "extraction_id": extraction_id, "updated_at": now}
    if reset:
        updated.update(pages = [], status = params.STATUS_QUEUED, error = None)
    return updated


def legacy_extraction_id(manifest: dict) -> str:
    """Stable ID for the one pre-run-layout extraction of a legacy book."""
    payload = json.dumps(manifest, sort_keys = True, separators = params.MANIFEST_JSON_SEPARATORS, default = str)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"legacy-{digest}"


def page_entry(*, page_no: int, key: str, chars: int, headings: int, tables: int, pictures: int, formulas: int,
               figures: list[str], section_path: list[str]) -> dict:
    """One extracted page as the manifest lists it (the page itself is in its own object)."""
    return {
        "page":         int(page_no),
        "key":          key,
        "chars":        int(chars),
        "headings":     int(headings),
        "tables":       int(tables),
        "pictures":     int(pictures),
        "formulas":     int(formulas),
        "figures":      figures,
        "section_path": section_path,
    }


def with_pages(manifest: dict, entries: list[dict], now: float) -> dict:
    """The manifest with `entries` added, a page extracted again replacing its old entry; pages stay in book order."""
    by_page = {entry["page"]: entry for entry in manifest.get("pages", [])}
    by_page.update({entry["page"]: entry for entry in entries})
    return {**manifest, "pages": [by_page[page] for page in sorted(by_page)], "updated_at": now}


def done_pages(manifest: dict) -> set[int]:
    return {entry["page"] for entry in manifest.get("pages", [])}


def entry_for_page(manifest: dict, page_no: int) -> dict | None:
    return next((entry for entry in manifest.get("pages", []) if entry["page"] == page_no), None)


def library_row(manifest: dict) -> dict:
    """A book as the library lists it: no outline, no page list."""
    return {
        "book_id":    manifest["book_id"],
        "title":      manifest.get("title", ""),
        "filename":   manifest.get("filename", ""),
        "size_bytes": manifest.get("size_bytes", 0),
        "n_pages":    manifest.get("n_pages", 0),
        "pages_done": len(manifest.get("pages", [])),
        "status":     manifest.get("status", params.STATUS_QUEUED),
        "created_at": manifest.get("created_at", 0.0),
        "updated_at": manifest.get("updated_at", 0.0),
    }


def newest_first(rows: list[dict]) -> list[dict]:
    return sorted(rows, key = lambda row: row.get("updated_at", 0.0), reverse = True)
