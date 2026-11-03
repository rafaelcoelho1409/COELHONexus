"""Key builders: everything of a book lives under `books/{book_id}/`, so wiping a book is one prefix delete."""
from __future__ import annotations


def books_prefix() -> str:
    return "books/"


def book_prefix(book_id: str) -> str:
    return f"{books_prefix()}{book_id.strip().strip('/')}/"


def source_key(book_id: str, source_format: str = "pdf") -> str:
    if source_format not in ("pdf", "epub"):
        raise ValueError(f"unsupported book format: {source_format}")
    return f"{book_prefix(book_id)}source.{source_format}"


def manifest_key(book_id: str) -> str:
    return f"{book_prefix(book_id)}manifest.json"


def page_key(book_id: str, page_no: int) -> str:
    """Zero-padded, so listing the pages alphabetically lists them in book order."""
    return f"{book_prefix(book_id)}pages/{page_no:04d}.md"


def structured_key(book_id: str, first: int, last: int) -> str:
    """The Docling document of one extraction slice (`first`..`last`), kept for the stages that read more than the markdown."""
    return f"{book_prefix(book_id)}structured/{first:04d}-{last:04d}.json"


def figure_key(book_id: str, name: str) -> str:
    """Only the last path component of `name` counts, so a name taken from a URL cannot climb out of the book's prefix."""
    return f"{book_prefix(book_id)}figures/{name.strip().replace(chr(92), '/').rsplit('/', 1)[-1].replace('..', '')}"
