"""Check of the real extractor — Docling and the PDF reader — on a real PDF. Downloads Docling's models
on the first run, so it is not part of the offline checks; it skips when no PDF is given.

    cd apps/server && uv sync && BD_TEST_PDF=/path/to/book.pdf .venv/bin/python ../../scripts/server/test_bd_ingestion_extract.py

`BD_TEST_PAGES` (default 8) is how many pages of the book are converted: the first slice, then the next one, to see that page numbers
stay the book's own across slices."""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

for name, value in {"REDIS_HOST": "localhost", "REDIS_PORT": "6379", "REDIS_PASSWORD": "x", "ENVIRONMENT": "local"}.items():
    os.environ.setdefault(name, value)
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "server"))

PDF = os.environ.get("BD_TEST_PDF")
if not PDF or not Path(PDF).is_file():
    print("skip  set BD_TEST_PDF to a PDF to check the real extractor")
    sys.exit(0)

import pypdfium2 as pdfium

import domains
import domains.bd.ingestion.extract.service
import domains.bd.ingestion.outline.service

ingestion = domains.bd.ingestion
PAGES = int(os.environ.get("BD_TEST_PAGES", "8"))
HALF = PAGES // 2


def check(label: str, got, want) -> None:
    assert got == want, f"{label}: expected {want!r}, got {got!r}"
    print(f"ok  {label}")


info = ingestion.outline.service.inspect_pdf(PDF)
print(f"    book: {info.n_pages} pages, title {info.title!r}, {len(info.outline)} outline entries")
check("the reader finds the pages", info.n_pages > 0, True)
check("the outline starts at level 1 and never skips a level",
      all(b.level <= a.level + 1 for a, b in zip(info.outline, info.outline[1:])) and (not info.outline or info.outline[0].level == 1), True)
check("every outline entry is on a page of the book", all(1 <= e.page <= info.n_pages for e in info.outline), True)

with tempfile.TemporaryDirectory() as workdir:
    subset = os.path.join(workdir, "subset.pdf")
    source = pdfium.PdfDocument(PDF)
    part = pdfium.PdfDocument.new()
    part.import_pages(source, list(range(min(PAGES, len(source)))))
    part.save(subset)
    part.close()
    source.close()

    started = time.time()
    first = ingestion.extract.service.convert_slice(subset, 1, HALF)
    print(f"    slice 1-{HALF}: {time.time() - started:.1f}s (includes loading the models)")
    started = time.time()
    second = ingestion.extract.service.convert_slice(subset, HALF + 1, PAGES)
    print(f"    slice {HALF + 1}-{PAGES}: {time.time() - started:.1f}s")

check("a slice returns every page asked for", [p.page_no for p in first.pages], list(range(1, HALF + 1)))
check("the next slice keeps the book's own page numbers", [p.page_no for p in second.pages], list(range(HALF + 1, PAGES + 1)))
check("pages carry markdown", all(isinstance(p.markdown, str) for p in first.pages + second.pages), True)
check("something was extracted", sum(len(p.markdown) for p in first.pages + second.pages) > 200, True)
headings = [h for p in first.pages + second.pages for h in p.headings]
print(f"    {len(headings)} headings, levels {sorted({h.level for h in headings})}")
check("headings have levels from 1 down", all(h.level >= 1 for h in headings), True)
figures = first.figures + second.figures
check("a figure is a PNG", all(f.png[:8] == b"\x89PNG\r\n\x1a\n" for f in figures), True)
check("a page lists the figures cropped from it", all(f.name in next(p for p in first.pages + second.pages if p.page_no == f.page_no).figures for f in figures), True)
check("the structured document keeps no pixels", "data:image" in json.dumps(first.structured), False)
print(f"    {sum(p.tables for p in first.pages + second.pages)} tables, {sum(p.pictures for p in first.pages + second.pages)} pictures, "
      f"{sum(p.formulas for p in first.pages + second.pages)} formulas, {len(figures)} figures cropped")
