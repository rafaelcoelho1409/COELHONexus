"""A read-only Deep Agents backend over a fixed set of files held in memory."""
from __future__ import annotations
from . import domain, params

import fnmatch
from collections.abc import Mapping

from deepagents.backends import CompositeBackend, StateBackend
from deepagents.backends.protocol import (
    BackendProtocol,
    EditResult,
    FileData,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)
from deepagents.backends.utils import slice_read_response


def mounted(files: Mapping[str, str], route: str) -> CompositeBackend:
    """The agent's backend with `files` readable under `route` (e.g. `/sources/`); everything else is its own scratch state."""
    return CompositeBackend(default = StateBackend(), routes = {route: FilesBackend(files, route = route)})


class FilesBackend(BackendProtocol):
    """`files` maps a path below the route (`/12-name.md`) to its text. Mount it on a route of a `CompositeBackend`: the route is
    stripped before a path reaches it. `ls` and `glob` list the files, `grep` searches their lines, `read_file` returns a window."""

    def __init__(self, files: Mapping[str, str], *, route: str) -> None:
        self._files = {"/" + path.strip("/"): text for path, text in files.items()}
        self._route = route

    def _path(self, path: str | None) -> str:
        return domain.relative_path(path, self._route)

    async def als(self, path: str) -> LsResult:
        if self._path(path).rstrip("/") not in ("", "/"):
            return LsResult(error = f"directory '{path}' not found")
        entries = [{"path": name, "is_dir": False} for name in sorted(self._files)]
        return LsResult(entries = entries[:params.LS_MAX_ENTRIES])

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        text = self._files.get(self._path(file_path))
        if text is None:
            return ReadResult(error = f"File '{file_path}' not found")
        if len(text) > params.MAX_FILE_CHARS:
            text = text[:params.MAX_FILE_CHARS] + f"\n\n[file cut at {params.MAX_FILE_CHARS} characters]"
        window = slice_read_response(FileData(content = text, encoding = "utf-8"), offset, limit)
        if isinstance(window, ReadResult):
            return window
        return ReadResult(file_data = FileData(content = window, encoding = "utf-8"))

    async def agrep(self, pattern: str, path: str | None = None, glob: str | None = None) -> GrepResult:
        needle = pattern.casefold()
        base = self._path(path) if path else "/"
        matches: list[dict] = []
        for name in sorted(self._files):
            if base not in ("/", name) and not name.startswith(base.rstrip("/") + "/"):
                continue
            if glob and not fnmatch.fnmatch(name.rsplit("/", 1)[-1], glob):
                continue
            for number, line in enumerate(self._files[name].splitlines(), start = 1):
                if needle in line.casefold():
                    matches.append({"path": name, "line": number, "text": line[:params.GREP_LINE_CHARS]})
                    if len(matches) >= params.GREP_MAX_MATCHES:
                        return GrepResult(matches = matches)
        return GrepResult(matches = matches)

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        base = self._path(path) if path else "/"
        wanted = pattern if pattern.startswith("/") else f"{base.rstrip('/')}/{pattern}"
        found = [{"path": name, "is_dir": False} for name in sorted(self._files) if fnmatch.fnmatch(name, self._path(wanted))]
        return GlobResult(matches = found)

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        return WriteResult(error = params.READ_ONLY_ERROR)

    async def aedit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False) -> EditResult:
        return EditResult(error = params.READ_ONLY_ERROR)
