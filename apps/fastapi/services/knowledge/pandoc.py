"""
Knowledge Distiller — Pandoc export service

Concatenates the study's markdown artifacts (summary.md + chapter READMEs)
and renders them into PDF / HTML / EPUB via pypandoc. All I/O goes through
MinIOStudyStorage — no local files except the short-lived temp file pypandoc
needs for binary formats.

PDF pipeline: markdown → pandoc → xelatex → PDF.
  xelatex is chosen over pdflatex because:
    - Proper Unicode support (code samples with non-ASCII identifiers)
    - Better font handling (lmodern fallback, TeX Gyre)
    - Handles edge cases in syntax-highlighted code blocks

HTML pipeline: markdown → pandoc → standalone HTML (single file, embedded CSS).
EPUB pipeline: markdown → pandoc → EPUB3 (binary, read from a tempfile).

Every export writes to:
    <study_root>/exports/study.<ext>

System requirements (installed in Dockerfile.fastapi):
    pandoc, texlive-xetex, texlive-fonts-recommended, lmodern
"""
import asyncio
import logging
import os
import tempfile
from typing import Literal

import pypandoc

from services.knowledge.storage import MinIOStudyStorage


logger = logging.getLogger(__name__)


PandocFormat = Literal["pdf", "html", "epub"]

# Chapters are named chapterNN/README.md with NN ∈ 01..12. We read whatever
# subset is actually present (early-stopped runs still export what's done).
_MAX_CHAPTERS = 12

_FORMAT_EXT = {
    "pdf": "pdf",
    "html": "html",
    "epub": "epub",
}

_FORMAT_CONTENT_TYPE = {
    "pdf": "application/pdf",
    "html": "text/html",
    "epub": "application/epub+zip",
}


async def _read_if_exists(
    storage: MinIOStudyStorage,
    key: str) -> str | None:
    """Read an object or return None if it's missing. No raise on 404."""
    if not await storage.exists(key):
        return None
    return await storage.read_text(key)


async def _assemble_markdown(
    storage: MinIOStudyStorage,
    study_root: str,
    framework: str) -> str:
    """
    Build the single concatenated markdown document that will be fed to pandoc.

    Order:
        1. Top-level heading "# {framework} — Knowledge Distiller Study"
        2. summary.md (if present — from Assembler node)
        3. Each chapter's README.md, separated by '\\n\\n---\\n\\n'
        4. DEBT.md as a final appendix (if any debt was recorded)

    Missing artifacts are skipped silently — a study can be exported mid-flight.
    """
    parts: list[str] = [f"# {framework} — Knowledge Distiller Study\n"]
    # Summary (reading plan, market roadmap)
    summary = await _read_if_exists(storage, f"{study_root}/summary.md")
    if summary:
        parts.append(summary.strip())
    # Chapters in numerical order
    for n in range(1, _MAX_CHAPTERS + 1):
        readme = await _read_if_exists(
            storage, f"{study_root}/chapter{n:02d}/README.md",
        )
        if readme:
            parts.append(readme.strip())
    # DEBT appendix
    debt = await _read_if_exists(storage, f"{study_root}/DEBT.md")
    if debt:
        parts.append("# Appendix — DEBT\n\n" + debt.strip())
    return "\n\n---\n\n".join(parts) + "\n"


def _pandoc_extra_args(
    fmt: PandocFormat,
    framework: str) -> list[str]:
    """
    Build the format-specific pandoc flags.

    PDF (xelatex):
      - xelatex engine for Unicode + good font handling
      - 2cm margins so code blocks have room to breathe
      - TOC + numbered sections so a long study is navigable
      - tango highlight style — close to IDE defaults, readable in PDF

    HTML:
      - standalone → single file with embedded CSS
      - self-contained flag retained for inline images if ever used
      - TOC inline at the top

    EPUB:
      - TOC depth 2 to keep the reader sidebar tidy
      - cover metadata is skipped — we don't have artwork
    """
    title = f"{framework} Knowledge Distiller Study"
    common = [
        "--standalone",
        "--toc",
        "--toc-depth=2",
        "--highlight-style=tango",
        "-V", f"title={title}",
        "-V", "author=COELHO Nexus — Knowledge Distiller",
    ]
    if fmt == "pdf":
        return common + [
            "--pdf-engine=xelatex",
            "-V", "geometry:margin=2cm",
            "-V", "mainfont=DejaVu Serif",
            "-V", "sansfont=DejaVu Sans",
            "-V", "monofont=DejaVu Sans Mono",
            "--number-sections",
        ]
    if fmt == "html":
        return common + [
            "--embed-resources",  # self-contained HTML — single file
            "--number-sections",
        ]
    if fmt == "epub":
        return common + [
            "-V", f"subtitle=Generated on demand from official docs",
        ]
    return common


def _render_with_pandoc(
    markdown_text: str,
    fmt: PandocFormat,
    outputfile: str,
    framework: str) -> None:
    """
    Synchronous pypandoc call. Wrapped in asyncio.to_thread by the caller so
    the FastAPI/Celery event loop isn't blocked during the subprocess run.
    """
    pypandoc.convert_text(
        source = markdown_text,
        to = fmt,
        format = "md",
        outputfile = outputfile,
        extra_args = _pandoc_extra_args(fmt, framework),
    )


async def render_study(
    storage: MinIOStudyStorage,
    study_root: str,
    framework: str,
    fmt: PandocFormat) -> tuple[str, int]:
    """
    Render the full study in `fmt`, upload to MinIO at
    <study_root>/exports/study.<ext>, return (object_key, bytes_written).

    Raises:
        FileNotFoundError: study_root has no readable artifacts at all.
        RuntimeError: pandoc subprocess failed (LaTeX error, missing fonts,
                      etc.). Message surfaces the pandoc stderr.
    """
    # 1) Assemble input markdown in memory
    markdown = await _assemble_markdown(storage, study_root, framework)
    if markdown.strip() == f"# {framework} — Knowledge Distiller Study":
        raise FileNotFoundError(
            f"No exportable artifacts found under {study_root!r} "
            "(no summary, no chapters)"
        )

    ext = _FORMAT_EXT[fmt]
    output_key = f"{study_root}/exports/study.{ext}"

    # 2) Run pandoc into a temp file
    #    pypandoc requires an outputfile path for binary formats; even HTML
    #    is simpler to route through a tempfile so the MinIO upload is uniform.
    tmp_dir = tempfile.mkdtemp(prefix = "kd-export-")
    tmp_path = os.path.join(tmp_dir, f"study.{ext}")
    try:
        try:
            await asyncio.to_thread(
                _render_with_pandoc,
                markdown, fmt, tmp_path, framework,
            )
        except Exception as e:
            raise RuntimeError(f"Pandoc render failed (fmt={fmt}): {e}") from e

        # 3) Upload the rendered file to MinIO
        with open(tmp_path, "rb") as fh:
            body = fh.read()
        bytes_written = await storage.write(
            key = output_key,
            content = body,
            content_type = _FORMAT_CONTENT_TYPE[fmt],
        )
        logger.info(
            f"[pandoc] rendered {fmt} ({bytes_written} bytes) → {output_key}"
        )
        return output_key, bytes_written
    finally:
        # Clean up the temp dir even on error — otherwise the worker's /tmp
        # fills up over many exports.
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            os.rmdir(tmp_dir)
        except OSError:
            pass
