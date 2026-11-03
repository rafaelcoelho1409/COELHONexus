"""Reads a PDF's page count or EPUB metadata and outline. This module stays out of the package's eager `__init__` chain so API requests that do
not extract books do not load the PDF reader; the book task imports it when extraction begins."""
from __future__ import annotations

import posixpath
import zipfile
from xml.etree import ElementTree

import pypdfium2 as pdfium

from . import domain, entities


def inspect_pdf(pdf_path: str) -> entities.PdfInfo:
    """Blocking (opens the file): run it in a thread from async code."""
    pdf = pdfium.PdfDocument(pdf_path)
    try:
        n_pages = len(pdf)
        rows = []
        for bookmark in pdf.get_toc():
            rows.append((bookmark.level, _bookmark_title(bookmark), _bookmark_page_index(bookmark)))
        title = " ".join(str((pdf.get_metadata_dict() or {}).get("Title", "")).split())
    finally:
        pdf.close()
    outline = domain.normalize(domain.entries_from_rows(rows), n_pages)
    return entities.PdfInfo(n_pages = n_pages, title = title, outline = tuple(outline))


def _bookmark_title(bookmark) -> str:
    get_title = getattr(bookmark, "get_title", None)
    return get_title() if callable(get_title) else bookmark.title


def _bookmark_page_index(bookmark) -> int | None:
    get_dest = getattr(bookmark, "get_dest", None)
    if not callable(get_dest):
        return bookmark.page_index
    destination = get_dest()
    return destination.get_index() if destination is not None else None


def inspect_epub(epub_path: str) -> entities.EpubInfo:
    """Read the package title from the EPUB container without extracting archive members to disk."""
    try:
        with zipfile.ZipFile(epub_path) as archive:
            if len(archive.filelist) > 1000:
                raise ValueError("the EPUB contains too many archive members")
            if _read_member(archive, "mimetype", 64) != b"application/epub+zip":
                raise ValueError("the EPUB mimetype marker is missing")
            container = ElementTree.fromstring(_read_member(archive, "META-INF/container.xml", 1024 * 1024))
            rootfiles = [element for element in container.iter() if _local_name(element.tag) == "rootfile"]
            package_path = next(
                (element.attrib.get("full-path", "") for element in rootfiles if element.attrib.get("full-path")),
                "",
            )
            normalized_path = posixpath.normpath(package_path)
            if not package_path or normalized_path.startswith(("/", "../")) or normalized_path == "..":
                raise ValueError("the EPUB package path is invalid")
            package = ElementTree.fromstring(_read_member(archive, normalized_path, 10 * 1024 * 1024))
            metadata = next((element for element in package if _local_name(element.tag) == "metadata"), None)
            spine = next((element for element in package if _local_name(element.tag) == "spine"), None)
            if metadata is None or spine is None or not any(_local_name(element.tag) == "itemref" for element in spine):
                raise ValueError("the EPUB package has no metadata or reading order")
            title = next(
                (" ".join((element.text or "").split()) for element in metadata.iter() if _local_name(element.tag) == "title" and (element.text or "").strip()),
                "",
            )
            return entities.EpubInfo(title = title)
    except (EOFError, KeyError, OSError, RuntimeError, ElementTree.ParseError, zipfile.BadZipFile) as error:
        raise ValueError(f"cannot read EPUB package metadata: {error}") from error


def _read_member(archive: zipfile.ZipFile, name: str, max_bytes: int) -> bytes:
    info = archive.getinfo(name)
    if info.file_size > max_bytes:
        raise ValueError(f"EPUB member {name!r} exceeds the metadata size limit")
    with archive.open(info) as member:
        data = member.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError(f"EPUB member {name!r} exceeds the metadata size limit")
    return data


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]
