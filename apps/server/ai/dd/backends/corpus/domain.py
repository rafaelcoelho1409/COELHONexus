"""Pure helpers of the corpus backend: file names, the header of a page file, the line `grep` searches."""
from __future__ import annotations
from . import entities, params

import re


def doc_slug(source_key: str) -> str:
    """The page's file name stem: the last path segment of its storage key, reduced to a safe token."""
    stem = source_key.rsplit("/", 1)[-1].removesuffix(".md")
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", stem).strip("-").lower() or "page"


def doc_path(idx: int, source_key: str) -> str:
    return f"{params.DOCS_DIR}/{idx}-{doc_slug(source_key)}.md"


def search_line(doc: entities.CorpusDoc) -> str:
    """The one line `grep` matches a page on: its title, role, summary and key terms."""
    return f"{doc.title} | {doc.role} | {doc.summary} | terms: {', '.join(doc.terms)}"


def header(doc: entities.CorpusDoc) -> str:
    """What a page file starts with, ahead of the page text."""
    return (
        f"# {doc.title}\n"
        f"role: {doc.role}\n"
        f"summary: {doc.summary}\n"
        f"key terms: {', '.join(doc.terms)}\n"
        f"source: {doc.key}\n\n---\n\n"
    )


def relative_path(path: str | None) -> str:
    """A path as this backend sees it. The composite backend strips the `/corpus/` route, but a bare call may not."""
    value = "/" + (path or "").strip().strip("/")
    route = params.ROUTE.rstrip("/")
    if value == route or value.startswith(route + "/"):
        value = value[len(route):] or "/"
    return value
