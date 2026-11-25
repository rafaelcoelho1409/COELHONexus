"""Compile the structured report into PDF using the local Pandoc and LuaLaTeX tools."""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import re
import signal
import tempfile
from pathlib import Path

import domains

from . import entities, params, versions

_ASSET_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+\.(?:svg|png)$")


async def _capture_version(command: tuple[str, ...]) -> str:
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"},
            stdout = asyncio.subprocess.PIPE,
            stderr = asyncio.subprocess.DEVNULL,
            start_new_session = True,
        )
    except FileNotFoundError as error:
        raise RuntimeError("the LaTeX PDF toolchain is incomplete") from error
    try:
        output, _ = await asyncio.wait_for(
            process.communicate(),
            timeout = params.VERSION_PROBE_TIMEOUT_SECONDS,
        )
    except TimeoutError as error:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        await process.communicate()
        raise RuntimeError("the LaTeX PDF toolchain version check timed out") from error
    except asyncio.CancelledError:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        with contextlib.suppress(Exception):
            await asyncio.shield(process.communicate())
        raise
    if process.returncode != 0 or not output or len(output) > params.MAX_VERSION_OUTPUT_BYTES:
        raise RuntimeError("the LaTeX PDF toolchain version check failed")
    return output.decode("utf-8", errors = "replace").strip()


async def renderer_version() -> str:
    """Fingerprint the report formatter and installed toolchain for cache invalidation."""
    package_versions = await _capture_version((
        "dpkg-query",
        "-W",
        "-f=${binary:Package}=${Version}\\n",
        "texlive-*",
        "fonts-*",
        "librsvg2-*",
        "libcairo2",
        "libglib2.0-0",
        "libpango-1.0-0",
        "libfontconfig1",
    ))
    tool_versions = await asyncio.gather(
        _capture_version(("pandoc", "--version")),
        _capture_version(("lualatex", "--version")),
        _capture_version(("rsvg-convert", "--version")),
    )
    fingerprint = "\\n".join((versions.PDF_RENDERER_VERSION, package_versions, *tool_versions))
    digest = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:16]
    return f"{versions.PDF_RENDERER_VERSION}-{digest}"


async def _run_process(
    command: tuple[str, ...],
    *,
    working_directory: Path,
    environment: dict[str, str],
    timeout_seconds: int,
    label: str,
) -> None:
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd = working_directory,
            env = environment,
            stdout = asyncio.subprocess.DEVNULL,
            stderr = asyncio.subprocess.DEVNULL,
            start_new_session = True,
        )
    except FileNotFoundError as error:
        raise RuntimeError(f"the {label} executable is not installed") from error
    try:
        await asyncio.wait_for(process.wait(), timeout = timeout_seconds)
    except TimeoutError as error:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        await process.wait()
        raise RuntimeError(f"the {label} process exceeded its time limit") from error
    except asyncio.CancelledError:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        with contextlib.suppress(Exception):
            await asyncio.shield(process.wait())
        raise
    if process.returncode != 0:
        raise RuntimeError(f"the {label} process failed")


def _write_assets(asset_list: tuple[entities.ReportAsset, ...], asset_directory: Path) -> list[Path]:
    if len(asset_list) > params.MAX_ASSET_COUNT:
        raise ValueError("the report contains too many visual assets")
    total_bytes = 0
    vector_asset_count = 0
    paths = []
    asset_directory.mkdir()
    for asset in asset_list:
        if not _ASSET_NAME_RE.fullmatch(asset.filename):
            raise ValueError("the report contains an invalid visual asset name")
        expected_media_type = "image/svg+xml" if asset.filename.endswith(".svg") else "image/png"
        if asset.media_type != expected_media_type:
            raise ValueError("the report contains a visual asset with an invalid media type")
        vector_asset_count += asset.filename.endswith(".svg")
        if vector_asset_count > params.MAX_VECTOR_ASSET_COUNT:
            raise ValueError("the report contains too many vector assets")
        total_bytes += len(asset.content)
        if total_bytes > params.MAX_ASSET_BYTES:
            raise ValueError("the report visual assets exceed the LaTeX render limit")
        path = asset_directory / asset.filename
        path.write_bytes(asset.content)
        paths.append(path)
    return paths


async def _convert_svg_assets(paths: list[Path], *, working_directory: Path, environment: dict[str, str]) -> None:
    svg_paths = [path for path in paths if path.suffix == ".svg"]
    semaphore = asyncio.Semaphore(params.SVG_CONCURRENCY)

    async def convert(path: Path) -> None:
        async with semaphore:
            await _run_process(
                (
                    "rsvg-convert",
                    "--format=pdf",
                    f"--output={path.with_suffix('.pdf')}",
                    str(path),
                ),
                working_directory = working_directory,
                environment = environment,
                timeout_seconds = params.SVG_TIMEOUT_SECONDS,
                label = "SVG converter",
            )

    await asyncio.gather(*(convert(path) for path in svg_paths))


async def _render_pdf(document: entities.LatexDocument) -> bytes:
    """Write intermediate TeX and compile it without shell escape using local assets."""
    source = document.source_json.encode("utf-8")
    if not source or len(source) > params.MAX_SOURCE_JSON_BYTES:
        raise ValueError("the Learning Guide source is empty or exceeds the LaTeX render limit")

    with tempfile.TemporaryDirectory(prefix = "bd-distill-latex-") as temporary_directory:
        work = Path(temporary_directory)
        asset_directory = work / "assets"
        assets = _write_assets(document.assets, asset_directory)
        (work / "report.json").write_bytes(source)

        texmf_directory = work / "texmf"
        texmf_directory.mkdir()
        (texmf_directory / "config").mkdir()
        (texmf_directory / "var").mkdir()
        environment = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "TMPDIR": str(work),
            "openin_any": "p",
            "openout_any": "p",
            "shell_escape": "f",
            "TEXMFHOME": str(texmf_directory),
            "TEXMFCONFIG": str(texmf_directory / "config"),
            "TEXMFVAR": str(texmf_directory / "var"),
            "TEXMFOUTPUT": str(work),
        }
        await _convert_svg_assets(assets, working_directory = work, environment = environment)
        await _run_process(
            (
                "pandoc",
                "--from=json",
                "--to=latex",
                "--standalone",
                "--toc",
                "--toc-depth=2",
                "--top-level-division=chapter",
                "--resource-path=.",
                "--output=report.tex",
                "-V",
                "documentclass=book",
                "-V",
                "classoption=oneside,openany",
                "-V",
                "geometry=margin=25mm",
                "-V",
                "fontsize=11pt",
                "-V",
                "mainfont=Latin Modern Roman",
                "-V",
                "colorlinks=true",
                "-V",
                "linkcolor=blue",
                "-V",
                "urlcolor=blue",
                "report.json",
            ),
            working_directory = work,
            environment = environment,
            timeout_seconds = params.PROCESS_TIMEOUT_SECONDS,
            label = "Pandoc",
        )

        tex_path = work / "report.tex"
        if not tex_path.is_file() or tex_path.stat().st_size > params.MAX_TEX_BYTES:
            raise RuntimeError("Pandoc did not produce a valid LaTeX source file")

        compile_command = (
            "lualatex",
            "-interaction=nonstopmode",
            "-halt-on-error",
            "-no-shell-escape",
            "-file-line-error",
            "-output-directory=.",
            "report.tex",
        )
        for _ in range(params.LATEX_PASSES):
            await _run_process(
                compile_command,
                working_directory = work,
                environment = environment,
                timeout_seconds = params.PROCESS_TIMEOUT_SECONDS,
                label = "LuaLaTeX",
            )

        pdf_path = work / "report.pdf"
        if (
            not pdf_path.is_file()
            or pdf_path.stat().st_size > domains.bd.distill.source.rendering.params.MAX_PDF_BYTES
        ):
            raise RuntimeError("LuaLaTeX did not produce a valid PDF artifact")
        pdf = pdf_path.read_bytes()
        if not pdf.startswith(b"%PDF-"):
            raise RuntimeError("LuaLaTeX returned an invalid PDF artifact")
        return pdf


async def render_pdf(document: entities.LatexDocument) -> bytes:
    """Bound the full conversion, including vector conversion and both LaTeX passes."""
    try:
        async with asyncio.timeout(params.TOTAL_RENDER_TIMEOUT_SECONDS):
            return await _render_pdf(document)
    except TimeoutError as error:
        raise RuntimeError("the Learning Guide exceeded the total PDF render time limit") from error
