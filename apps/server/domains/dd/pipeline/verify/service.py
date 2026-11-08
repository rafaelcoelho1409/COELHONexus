"""Verifying the code of a chapter: the static checks of every block, then a run of each block that got far enough when an executor is given."""
from __future__ import annotations
import domains
from . import domain, entities, params

import asyncio
import hashlib
import os
import resource
import sys
import tempfile
import time


class SubprocessExecutor:
    """Runs Python in a child process of the worker: a fresh interpreter in isolated mode, in an empty temporary directory, with a stripped environment, limits
    on CPU time, memory, open files and processes, a wall-clock timeout and its output cut. It does NOT cut the network or hide the host's files: that is the
    deployment's job (a sandbox pod with no egress and no secrets). `params.RUN_ENABLED` says whether a run uses it at all."""

    async def run(self, code: str, *, lang: str, timeout_s: float) -> entities.RunResult:
        if lang != "python":
            return entities.RunResult(ok = False, stderr = f"only Python is run, not {lang or 'text'}")
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix = "dd-run-") as directory:
            with open(os.path.join(directory, "main.py"), "w", encoding = "utf-8") as handle:
                handle.write(code)

            def limit() -> None:
                resource.setrlimit(resource.RLIMIT_CPU, (int(timeout_s) + params.RUN_CPU_GRACE_S,) * 2)
                resource.setrlimit(resource.RLIMIT_AS, (params.RUN_MEMORY_BYTES,) * 2)
                resource.setrlimit(resource.RLIMIT_NOFILE, (params.RUN_OPEN_FILES,) * 2)
                resource.setrlimit(resource.RLIMIT_NPROC, (params.RUN_PROCESSES,) * 2)

            process = await asyncio.create_subprocess_exec(
                sys.executable, "-I", "main.py", cwd = directory, stdout = asyncio.subprocess.PIPE, stderr = asyncio.subprocess.PIPE,
                env = {"PATH": "/usr/bin:/bin", "HOME": directory, "TMPDIR": directory, "PYTHONDONTWRITEBYTECODE": "1"}, preexec_fn = limit,
            )
            try:
                out, err = await asyncio.wait_for(process.communicate(), timeout_s)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                return entities.RunResult(ok = False, stderr = f"timed out after {timeout_s:g} s", seconds = time.monotonic() - started)
        return entities.RunResult(
            ok = process.returncode == 0,
            stdout = out.decode("utf-8", "replace")[-params.MAX_OUTPUT_CHARS:],
            stderr = err.decode("utf-8", "replace")[-params.MAX_OUTPUT_CHARS:],
            seconds = time.monotonic() - started,
        )


def executor() -> entities.Executor | None:
    """The executor a run uses: the subprocess one when running code is enabled, else none."""
    return SubprocessExecutor() if params.RUN_ENABLED else None


async def verify_blocks(
    blocks: list[tuple[str, str, str]],
    environment: entities.Environment,
    executor: entities.Executor | None = None,
) -> entities.Report:
    """The report for `(hash, language, code)` blocks. A block is run only when it is Python that resolved and is more than a fragment; a run that
    fails, or an executor that raises, is a failure of the block, never of the report."""
    results = []
    for digest, lang, body in blocks:
        result = domain.verify_block(digest, lang, body, environment)
        runnable = result.level == "resolved" and result.ok and len(body.splitlines()) >= params.MIN_RUN_LINES
        if executor is not None and runnable:
            try:
                run = await executor.run(body, lang = lang, timeout_s = params.RUN_TIMEOUT_S)
                result = domain.after_run(result, run)
            except Exception as error:
                result = entities.Result(hash = digest, lang = lang, level = result.level, ok = False, issues = (f"the run could not finish: {type(error).__name__}: {str(error)[:120]}",))
        results.append(result)
    return entities.Report(results = tuple(results))


async def verify_ledger(
    ledger: domains.dd.pipeline.ledger.entities.Ledger,
    environment: entities.Environment,
    executor: entities.Executor | None = None,
) -> entities.Report:
    """The report for the blocks a chapter has to teach."""
    blocks = [(digest, ledger.blocks[digest].lang, ledger.blocks[digest].body) for digest in ledger.must_teach]
    return await verify_blocks(blocks, environment, executor)


async def verify_text(markdown: str, environment: entities.Environment, executor: entities.Executor | None = None) -> entities.Report:
    """The report for every fenced block a draft contains. Blocks are named by the hash of their code."""
    blocks = [(hashlib.sha256(body.encode("utf-8")).hexdigest()[:16], lang, body) for lang, body in domain.extract_fences(markdown)]
    return await verify_blocks(blocks, environment, executor)
