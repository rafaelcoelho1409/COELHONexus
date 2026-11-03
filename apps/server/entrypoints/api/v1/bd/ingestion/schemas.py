"""HTTP shapes of Book Distiller ingestion — the contract the generated web client is built from."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class StartResponse(BaseModel):
    status: Literal["queued", "cached", "locked"]
    book_id: str
    run_id: str | None
    resumed: bool


class BookRow(BaseModel):
    book_id: str
    title: str
    filename: str
    size_bytes: int
    n_pages: int
    pages_done: int
    status: str
    created_at: float
    updated_at: float


class OutlineEntry(BaseModel):
    level: int
    title: str
    page: int


class ChapterRange(BaseModel):
    title: str
    first_page: int
    last_page: int


class PageEntry(BaseModel):
    page: int
    key: str
    chars: int
    headings: int
    tables: int
    pictures: int
    formulas: int
    figures: list[str]
    section_path: list[str]


class Manifest(BaseModel):
    book_id: str
    title: str
    filename: str
    size_bytes: int
    n_pages: int
    status: str
    error: str | None
    outline: list[OutlineEntry]
    chapters: list[ChapterRange]
    pages: list[PageEntry]
    created_at: float
    updated_at: float


class Progress(BaseModel):
    run_id: str
    status: str
    completed: int
    total: int
    error: str | None
    started_at: float
    updated_at: float


class Snapshot(BaseModel):
    manifest: Manifest
    progress: Progress | None


class ActiveRun(BaseModel):
    book_id: str
    run_id: str
    progress: Progress


class ActiveRuns(BaseModel):
    active: list[ActiveRun]


class PageBody(BaseModel):
    book_id: str
    page: int
    markdown: str


class CancelResponse(BaseModel):
    book_id: str
    status: str


class DeleteResponse(BaseModel):
    book_id: str
    deleted: bool
    reason: str | None = None
    objects: int | None = None
