from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DoclingConfig:
    """What the extractor asks of Docling. Born-digital books need no OCR (it triples the time and can only hurt a clean text layer);
    flip `ocr` for a scan. The enrichments Docling runs with a vision model (figure descriptions, formulas as LaTeX, chart tables)
    stay off: they are a later stage that reads the structured output kept in the store."""
    ocr: bool = False
    table_structure: bool = True
    heading_hierarchy: bool = True
    figures: bool = True
    images_scale: float = 2.0


DOCLING = DoclingConfig()
