"""A read-only Deep Agents backend over a fixed set of files held in memory."""
from __future__ import annotations
from . import domain, params

import fnmatch
import collections.abc

import deepagents.backends
import deepagents.backends.protocol
import deepagents.backends.utils


def mounted(files: collections.abc.Mapping[str, str], route: str) -> deepagents.backends.CompositeBackend:
    """The agent's backend with `files` readable under `route` (e.g. `/sources/`); everything else is its own scratch state."""
    return deepagents.backends.CompositeBackend(default = deepagents.backends.StateBackend(), routes = {route: FilesBackend(files, route = route)})


class FilesBackend(deepagents.backends.protocol.BackendProtocol):
    """`files` maps a path below the route (`/12-name.md`) to its text. Mount it on a route of a `CompositeBackend`: the route is
    stripped before a path reaches it. `ls` and `glob` list the files, `grep` searches their lines, `read_file` returns a window."""

    def __init__(self, files: collections.abc.Mapping[str, str], *, route: str) -> None:
        self._files = {"/" + path.strip("/"): text for path, text in files.items()}
        self._route = route

    def _path(self, path: str | None) -> str:
        return domain.relative_path(path, self._route)

    async def als(self, path: str) -> deepagents.backends.protocol.LsResult:
        if self._path(path).rstrip("/") not in ("", "/"):
            return deepagents.backends.protocol.LsResult(error = f"directory '{path}' not found")
        entries = [{"path": name, "is_dir": False} for name in sorted(self._files)]
        return deepagents.backends.protocol.LsResult(entries = entries[:params.LS_MAX_ENTRIES])

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> deepagents.backends.protocol.ReadResult:
        text = self._files.get(self._path(file_path))
        if text is None:
            return deepagents.backends.protocol.ReadResult(error = f"File '{file_path}' not found")
        if len(text) > params.MAX_FILE_CHARS:
            text = text[:params.MAX_FILE_CHARS] + f"\n\n[file cut at {params.MAX_FILE_CHARS} characters]"
        window = deepagents.backends.utils.slice_read_response(deepagents.backends.protocol.FileData(content = text, encoding = "utf-8"), offset, limit)
        if isinstance(window, deepagents.backends.protocol.ReadResult):
            return window
        return deepagents.backends.protocol.ReadResult(file_data = deepagents.backends.protocol.FileData(content = window, encoding = "utf-8"))

    async def agrep(self, pattern: str, path: str | None = None, glob: str | None = None) -> deepagents.backends.protocol.GrepResult:
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
                        return deepagents.backends.protocol.GrepResult(matches = matches)
        return deepagents.backends.protocol.GrepResult(matches = matches)

    async def aglob(self, pattern: str, path: str | None = None) -> deepagents.backends.protocol.GlobResult:
        base = self._path(path) if path else "/"
        wanted = pattern if pattern.startswith("/") else f"{base.rstrip('/')}/{pattern}"
        found = [{"path": name, "is_dir": False} for name in sorted(self._files) if fnmatch.fnmatch(name, self._path(wanted))]
        return deepagents.backends.protocol.GlobResult(matches = found)

    async def awrite(self, file_path: str, content: str) -> deepagents.backends.protocol.WriteResult:
        return deepagents.backends.protocol.WriteResult(error = params.READ_ONLY_ERROR)

    async def aedit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False) -> deepagents.backends.protocol.EditResult:
        return deepagents.backends.protocol.EditResult(error = params.READ_ONLY_ERROR)
