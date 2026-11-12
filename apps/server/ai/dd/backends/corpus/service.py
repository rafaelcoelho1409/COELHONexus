"""A read-only Deep Agents backend over the planner's corpus: page summaries are searchable in memory, page text is fetched from storage on demand."""
from __future__ import annotations
from . import domain, entities, params

import fnmatch
import collections.abc

import deepagents.backends.protocol
import deepagents.backends.utils


PageLoader = collections.abc.Callable[[str], collections.abc.Awaitable[str]]


class CorpusBackend(deepagents.backends.protocol.BackendProtocol):
    """`/docs/<idx>-<slug>.md` for every page of the corpus. Mount it on a route of a `CompositeBackend` (the route is stripped
    before a path reaches it). `ls`, `glob` and `grep` work on the summaries held in memory; `read_file` loads the page text
    through `load` and caches it, so a page the agent reads twice is fetched once."""

    def __init__(self, docs: collections.abc.Sequence[entities.CorpusDoc], load: PageLoader) -> None:
        self._docs = {doc.path: doc for doc in docs}
        self._load = load
        self._text: dict[str, str] = {}

    async def als(self, path: str) -> deepagents.backends.protocol.LsResult:
        base = domain.relative_path(path).rstrip("/") or "/"
        if base == "/":
            return deepagents.backends.protocol.LsResult(entries = [{"path": params.DOCS_DIR + "/", "is_dir": True}])
        if base != params.DOCS_DIR:
            return deepagents.backends.protocol.LsResult(error = f"directory '{path}' not found")
        entries = [{"path": doc_path, "is_dir": False} for doc_path in sorted(self._docs, key = lambda p: self._docs[p].idx)]
        return deepagents.backends.protocol.LsResult(entries = entries[:params.LS_MAX_ENTRIES])

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> deepagents.backends.protocol.ReadResult:
        doc = self._docs.get(domain.relative_path(file_path))
        if doc is None:
            return deepagents.backends.protocol.ReadResult(error = f"File '{file_path}' not found")
        if doc.path not in self._text:
            try:
                body = await self._load(doc.key)
            except Exception as error:
                return deepagents.backends.protocol.ReadResult(error = f"page '{file_path}' could not be read ({type(error).__name__})")
            if len(body) > params.MAX_PAGE_CHARS:
                body = body[:params.MAX_PAGE_CHARS] + f"\n\n[page cut at {params.MAX_PAGE_CHARS} characters]"
            self._text[doc.path] = domain.header(doc) + body
        window = deepagents.backends.utils.slice_read_response(deepagents.backends.protocol.FileData(content = self._text[doc.path], encoding = "utf-8"), offset, limit)
        if isinstance(window, deepagents.backends.protocol.ReadResult):
            return window
        return deepagents.backends.protocol.ReadResult(file_data = deepagents.backends.protocol.FileData(content = window, encoding = "utf-8"))

    async def agrep(self, pattern: str, path: str | None = None, glob: str | None = None) -> deepagents.backends.protocol.GrepResult:
        needle = pattern.casefold()
        base = domain.relative_path(path) if path else "/"
        matches = []
        for doc in sorted(self._docs.values(), key = lambda d: d.idx):
            if not doc.path.startswith(base.rstrip("/") + "/") and doc.path != base:
                continue
            if glob and not fnmatch.fnmatch(doc.path.rsplit("/", 1)[-1], glob):
                continue
            line = domain.search_line(doc)
            if needle in line.casefold():
                matches.append({"path": doc.path, "line": 1, "text": line})
                if len(matches) >= params.GREP_MAX_MATCHES:
                    break
        return deepagents.backends.protocol.GrepResult(matches = matches)

    async def aglob(self, pattern: str, path: str | None = None) -> deepagents.backends.protocol.GlobResult:
        base = domain.relative_path(path) if path else "/"
        wanted = pattern if pattern.startswith("/") else f"{base.rstrip('/')}/{pattern}"
        found = [
            {"path": doc.path, "is_dir": False}
            for doc in sorted(self._docs.values(), key = lambda d: d.idx)
            if fnmatch.fnmatch(doc.path, domain.relative_path(wanted))
        ]
        return deepagents.backends.protocol.GlobResult(matches = found)

    async def awrite(self, file_path: str, content: str) -> deepagents.backends.protocol.WriteResult:
        return deepagents.backends.protocol.WriteResult(error = params.READ_ONLY_ERROR)

    async def aedit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False) -> deepagents.backends.protocol.EditResult:
        return deepagents.backends.protocol.EditResult(error = params.READ_ONLY_ERROR)
