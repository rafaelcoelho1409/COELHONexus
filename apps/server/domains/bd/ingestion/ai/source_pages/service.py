"""Bounded rendering of original PDF pages for autonomous OCR-patch verification."""
from __future__ import annotations
from .. import params

import asyncio
import inspect
import io
import logging
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor

from pypdfium2 import PdfDocument


logger = logging.getLogger(__name__)
PdfReader = Callable[[], bytes | Awaitable[bytes]]


class PdfPageImageReader:
    """Render a requested physical page once and reuse the opened PDF for this agentic run."""

    def __init__(
        self,
        read_pdf: PdfReader,
        *,
        source_size_bytes: int | None,
        expected_page_count: int,
    ) -> None:
        self._read_pdf = read_pdf
        self._source_size_bytes = source_size_bytes
        self._expected_page_count = expected_page_count
        self._lock = asyncio.Lock()
        self._executor: ThreadPoolExecutor | None = None
        self._document = None
        self._unavailable_reason: str | None = None

    @property
    def unavailable_reason(self) -> str | None:
        return self._unavailable_reason

    async def read_page(self, page_number: int) -> bytes | None:
        """Return a small JPEG page image, or None when visual evidence is unavailable."""
        if type(page_number) is not int or not 1 <= page_number <= self._expected_page_count:
            self._unavailable_reason = "source page number is outside the original PDF"
            return None
        if (
            self._source_size_bytes is not None
            and self._source_size_bytes > params.SOURCE_PDF_VERIFY_MAX_BYTES
        ):
            self._unavailable_reason = "original PDF exceeds the visual-verification size limit"
            return None

        async with self._lock:
            try:
                await self._ensure_open()
                if self._document is None or self._executor is None:
                    return None
                loop = asyncio.get_running_loop()
                return await loop.run_in_executor(
                    self._executor,
                    self._render_page,
                    self._document,
                    page_number - 1,
                )
            except Exception as error:
                self._unavailable_reason = f"PDF page rendering failed ({type(error).__name__})"
                logger.warning(
                    f"[bd-page-evidence] page {page_number}: {self._unavailable_reason}",
                )
                return None

    async def close(self) -> None:
        """Release PDFium resources and the dedicated rendering thread."""
        async with self._lock:
            executor, document = self._executor, self._document
            self._executor = None
            self._document = None
            if executor is not None:
                loop = asyncio.get_running_loop()
                try:
                    if document is not None:
                        await loop.run_in_executor(executor, document.close)
                finally:
                    executor.shutdown(wait = True, cancel_futures = True)

    async def _ensure_open(self) -> None:
        if self._document is not None:
            return
        content = self._read_pdf()
        if inspect.isawaitable(content):
            content = await content
        if not isinstance(content, bytes) or not content.startswith(b"%PDF-"):
            self._unavailable_reason = "the original source is not a readable PDF"
            return
        if len(content) > params.SOURCE_PDF_VERIFY_MAX_BYTES:
            self._unavailable_reason = "original PDF exceeds the visual-verification size limit"
            return
        self._executor = ThreadPoolExecutor(max_workers = 1, thread_name_prefix = "bd-pdf-evidence")
        loop = asyncio.get_running_loop()
        try:
            self._document = await loop.run_in_executor(self._executor, PdfDocument, content)
            page_count = await loop.run_in_executor(self._executor, len, self._document)
            if page_count != self._expected_page_count:
                self._unavailable_reason = "original PDF page count does not match the extraction"
                document = self._document
                self._document = None
                await loop.run_in_executor(self._executor, document.close)
                self._executor.shutdown(wait = True, cancel_futures = True)
                self._executor = None
        except Exception:
            executor, document = self._executor, self._document
            self._executor = None
            self._document = None
            if executor is not None:
                try:
                    if document is not None:
                        await loop.run_in_executor(executor, document.close)
                except Exception as cleanup_error:
                    logger.warning(
                        f"[bd-page-evidence] PDF cleanup failed ({type(cleanup_error).__name__})",
                    )
                finally:
                    executor.shutdown(wait = True, cancel_futures = True)
            raise

    @staticmethod
    def _render_page(document, page_index: int) -> bytes:
        page = document[page_index]
        bitmap = None
        source_image = None
        image = None
        try:
            bitmap = page.render(scale = params.SOURCE_PAGE_IMAGE_SCALE)
            source_image = bitmap.to_pil()
            image = source_image.convert("RGB")
            image.thumbnail((
                params.SOURCE_PAGE_IMAGE_MAX_DIMENSION,
                params.SOURCE_PAGE_IMAGE_MAX_DIMENSION,
            ))
            output = io.BytesIO()
            image.save(
                output,
                format = "JPEG",
                quality = params.SOURCE_PAGE_IMAGE_JPEG_QUALITY,
                optimize = True,
            )
            return output.getvalue()
        finally:
            if image is not None:
                image.close()
            if source_image is not None and source_image is not image:
                source_image.close()
            if bitmap is not None:
                bitmap.close()
            page.close()
