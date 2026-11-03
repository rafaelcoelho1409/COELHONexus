"""Docling wrapper — the only module that touches the library. This stays out of the package's eager `__init__` chain so the API and
non-book Celery tasks do not load torch or Docling's layout models at startup; the book task imports it when extraction begins."""
from __future__ import annotations
from . import config, domain, entities, params

import functools
import io
import logging

from docling.datamodel.base_models import InputFormat
from docling.datamodel.backend_options import EpubBackendOptions
from docling.datamodel.pipeline_options import HeadingHierarchyOptions, PdfPipelineOptions
from docling.document_converter import DocumentConverter, EpubFormatOption, PdfFormatOption
from docling_core.types.doc import DocItemLabel, PictureItem, SectionHeaderItem, TableItem



logger = logging.getLogger(__name__)


@functools.cache
def converter():
    """The Docling converter, built once per process for PDFs and EPUBs."""
    pdf_options = PdfPipelineOptions(artifacts_path = params.ARTIFACTS_PATH)
    pdf_options.do_ocr = config.DOCLING.ocr
    pdf_options.do_table_structure = config.DOCLING.table_structure
    pdf_options.generate_picture_images = config.DOCLING.figures
    pdf_options.images_scale = config.DOCLING.images_scale
    pdf_options.heading_hierarchy_options = HeadingHierarchyOptions(enabled = config.DOCLING.heading_hierarchy)
    epub_options = EpubBackendOptions(fetch_images = True, enable_local_fetch = True, enable_remote_fetch = False)
    return DocumentConverter(format_options = {
        InputFormat.PDF:  PdfFormatOption(pipeline_options = pdf_options),
        InputFormat.EPUB: EpubFormatOption(backend_options = epub_options),
    })


def convert_slice(pdf_path: str, first: int, last: int) -> entities.SliceResult:
    """Extract pages `first`..`last` (1-based, inclusive) of the PDF. Blocking and CPU-bound: run it in a thread from async code.
    Every page of the slice comes back, even one with nothing on it, so the caller can tell a blank page from a missing one."""
    document = converter().convert(pdf_path, page_range = (first, last)).document
    headings: dict[int, list[entities.Heading]] = {}
    counts: dict[int, dict[str, int]] = {}
    figures: list[entities.Figure] = []
    figure_names: dict[int, list[str]] = {}

    for item, _ in document.iterate_items():
        provenance = getattr(item, "prov", None)
        if not provenance:
            continue
        page_no = provenance[0].page_no
        page_counts = counts.setdefault(page_no, {"tables": 0, "pictures": 0, "formulas": 0})
        if isinstance(item, SectionHeaderItem):
            headings.setdefault(page_no, []).append(entities.Heading(level = item.level, text = item.text))
        elif isinstance(item, TableItem):
            page_counts["tables"] += 1
        elif isinstance(item, PictureItem):
            page_counts["pictures"] += 1
            figure = _figure(item, document, page_no, len(figure_names.get(page_no, [])) + 1)
            if figure is not None:
                figures.append(figure)
                figure_names.setdefault(page_no, []).append(figure.name)
        elif getattr(item, "label", None) == DocItemLabel.FORMULA:
            page_counts["formulas"] += 1

    pages = tuple(
        entities.PageExtract(
            page_no = page_no,
            markdown = document.export_to_markdown(page_no = page_no),
            headings = tuple(headings.get(page_no, [])),
            tables = counts.get(page_no, {}).get("tables", 0),
            pictures = counts.get(page_no, {}).get("pictures", 0),
            formulas = counts.get(page_no, {}).get("formulas", 0),
            figures = tuple(figure_names.get(page_no, [])),
        )
        for page_no in sorted(document.pages)
    )
    structured = domain.strip_images(document.export_to_dict())
    return entities.SliceResult(pages = pages, figures = tuple(figures), structured = structured)


def convert_epub(epub_path: str) -> entities.SliceResult:
    """Convert an EPUB once, then divide its ordered Markdown into reader-sized sections."""
    document = converter().convert(epub_path).document
    markdown = document.export_to_markdown()
    sections = domain.epub_sections(markdown)
    top_level = domain.epub_section_level(markdown)
    counts = [{"tables": 0, "pictures": 0, "formulas": 0} for _ in sections]
    figure_names: list[list[str]] = [[] for _ in sections]
    figures: list[entities.Figure] = []
    section_index = 0
    saw_top_level = False

    for item, _ in document.iterate_items():
        if isinstance(item, SectionHeaderItem) and item.level == top_level:
            if saw_top_level:
                section_index += 1
            else:
                saw_top_level = True
        section_index = min(section_index, len(sections) - 1)
        section_counts = counts[section_index]
        if isinstance(item, TableItem):
            section_counts["tables"] += 1
        elif isinstance(item, PictureItem):
            section_counts["pictures"] += 1
            figure = _epub_figure(item, document, section_index + 1, len(figure_names[section_index]) + 1)
            if figure is not None:
                figures.append(figure)
                figure_names[section_index].append(figure.name)
        elif getattr(item, "label", None) == DocItemLabel.FORMULA:
            section_counts["formulas"] += 1

    pages = tuple(
        entities.PageExtract(
            page_no = section.page_no,
            markdown = section.markdown,
            headings = section.headings,
            tables = counts[index]["tables"],
            pictures = counts[index]["pictures"],
            formulas = counts[index]["formulas"],
            figures = tuple(figure_names[index]),
        )
        for index, section in enumerate(sections)
    )
    return entities.SliceResult(
        pages = pages,
        figures = tuple(figures),
        structured = domain.strip_images(document.export_to_dict()),
    )


def _figure(item, document, page_no: int, index: int) -> entities.Figure | None:
    """The picture cropped from its page as a PNG, or None when Docling kept no pixels for it."""
    image = item.get_image(document)
    if image is None:
        return None
    buffer = io.BytesIO()
    image.save(buffer, format = params.FIGURE_FORMAT)
    box = item.prov[0].bbox
    return entities.Figure(
        page_no = page_no,
        index = index,
        name = domain.figure_name(page_no, index),
        png = buffer.getvalue(),
        bbox = (box.l, box.t, box.r, box.b) if box is not None else None,
        caption = item.caption_text(document),
    )


def _epub_figure(item, document, section_no: int, index: int) -> entities.Figure | None:
    image = item.get_image(document)
    if image is None:
        return None
    buffer = io.BytesIO()
    image.save(buffer, format = params.FIGURE_FORMAT)
    return entities.Figure(
        page_no = section_no,
        index = index,
        name = domain.epub_figure_name(section_no, index),
        png = buffer.getvalue(),
        bbox = None,
        caption = item.caption_text(document),
    )
