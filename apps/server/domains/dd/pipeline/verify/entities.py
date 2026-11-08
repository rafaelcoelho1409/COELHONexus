"""What verifying a chapter's code produces."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class Environment:
    """What the checks may rely on. `known` is the modules the docs' own framework provides (`deepagents`, `langgraph`): they are expected to be
    importable and are not looked for in the worker, which does not install every framework it teaches."""
    known: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class Result:
    """What is known about one block. `level` is how far it got (`none`, `parsed`, `resolved`, `ran`); `ok` is False when it is wrong, True when it
    passed every check it was given, None when no check applies to its language. `issues` say why a block is wrong or what could not be checked."""
    hash: str
    lang: str
    level: str
    ok: bool | None
    issues: tuple[str, ...] = ()

    @property
    def status(self) -> str:
        if self.ok is False:
            return "failed"
        if self.level in ("resolved", "ran") and self.ok:
            return "verified"
        return "unverified"


@dataclass(frozen=True, slots=True)
class RunResult:
    ok: bool
    stdout: str = ""
    stderr: str = ""
    seconds: float = 0.0


class Executor(Protocol):
    """Runs a block of code in isolation and says how it ended. The sandbox backend implements it; without one a block's level stops at `resolved`."""

    async def run(self, code: str, *, lang: str, timeout_s: float) -> RunResult: ...


@dataclass(frozen=True, slots=True)
class Report:
    results: tuple[Result, ...]

    def count(self, status: str) -> int:
        return sum(1 for result in self.results if result.status == status)

    @property
    def failed(self) -> tuple[Result, ...]:
        return tuple(result for result in self.results if result.status == "failed")

    def summary(self) -> dict:
        return {status: self.count(status) for status in ("verified", "unverified", "failed")} | {"total": len(self.results)}
