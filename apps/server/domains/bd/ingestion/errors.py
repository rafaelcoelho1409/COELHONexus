from __future__ import annotations


class Cancelled(Exception):
    """The user asked for this book's extraction to stop; raised between slices."""

    def __init__(self, book_id: str) -> None:
        super().__init__(f"extraction of book {book_id} cancelled")
        self.book_id = book_id


class UnreadableBook(ValueError):
    """The file is not a PDF the extractor can open, or it is beyond what a book may be."""
