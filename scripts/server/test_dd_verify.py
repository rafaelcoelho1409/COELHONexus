"""Offline checks of the static verification of a chapter's code: parse, imports, configuration files, fenced blocks of a draft, and the run through an
executor (a fake one here). Nothing is executed.

    cd apps/server && .venv/bin/python ../../scripts/server/test_dd_verify.py
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "server"))

import domains

os.environ.setdefault("DD_STUDY_SEM", "1")

verify = domains.dd.pipeline.verify
domain = verify.domain
service = verify.service
entities = verify.entities

ENV = entities.Environment(known = frozenset({"deepagents"}))
GOOD = "import os\nfrom deepagents import create_deep_agent\n\nagent = create_deep_agent()\nprint(os.name)\n"


def check(label: str, got, want) -> None:
    assert got == want, f"{label}: expected {want!r}, got {got!r}"
    print(f"ok  {label}")


class FakeExecutor:
    def __init__(self, ok: bool = True, raises: bool = False) -> None:
        self.ok = ok
        self.raises = raises
        self.ran: list[str] = []

    async def run(self, code, *, lang, timeout_s):
        self.ran.append(code)
        if self.raises:
            raise TimeoutError("sandbox down")
        return entities.RunResult(ok = self.ok, stderr = "" if self.ok else "Traceback ...\nNameError: name 'x' is not defined")


def test_domain() -> None:
    check("modules: stdlib, the docs' framework, installed, missing",
          [domain.module_state(name, ENV) for name in ("os", "deepagents", "pydantic", "no_such_module_xyz")], ["stdlib", "known", "installed", "missing"])
    check("python that resolves is checked up to its imports", domain.check_python(GOOD, ENV), ("resolved", True, ()))
    check("python that does not parse is wrong", domain.check_python("def f(:\n", ENV), ("none", False, ("does not parse",)))
    level, ok, issues = domain.check_python("import no_such_module_xyz\nx = 1\n", ENV)
    check("an import nothing provides leaves the block unchecked, not wrong", (level, ok, "no_such_module_xyz" in issues[0]), ("parsed", None, True))
    check("an interactive session has no verdict", domain.check_python(">>> 1 + 1\n2", ENV)[1], None)
    check("json: valid and invalid", [domain.check_config("json", text)[:2] for text in ('{"a": 1}', '{"a": }')], [("parsed", True), ("none", False)])
    check("toml and yaml parse too", [domain.check_config(lang, text)[:2] for lang, text in (("toml", "a = 1"), ("yaml", "a: 1"), ("yml", "a: [1"))],
          [("parsed", True), ("parsed", True), ("none", False)])
    check("a language with no check is unverified, not verified", domain.verify_block("h", "bash", "ls", ENV).status, "unverified")
    check("javascript has no check", domain.verify_block("h", "javascript", "let x = 1", ENV).level, "none")
    check("status: resolved python is verified, broken is failed, unchecked import is unverified",
          [domain.verify_block("h", "python", body, ENV).status for body in (GOOD, "def f(:\n", "import no_such_module_xyz\nx = 1\n")],
          ["verified", "failed", "unverified"])


def test_fences() -> None:
    text = "Intro\n\n```python\nx = 1\n```\n\n~~~json\n{}\n~~~\n\n```\nplain\n```\n\n````python\ny = 2\n````\n"
    check("every fenced block of a draft, with its language", domain.extract_fences(text),
          [("python", "x = 1"), ("json", "{}"), ("", "plain"), ("python", "y = 2")])
    check("an unclosed fence yields nothing", domain.extract_fences("```python\nx = 1\n"), [])


async def test_service() -> None:
    blocks = [("a", "python", GOOD), ("b", "python", "def f(:\n"), ("c", "bash", "ls -la"), ("d", "python", "x = 1")]
    report = await service.verify_blocks(blocks, ENV)
    check("without an executor nothing reaches `ran`", [result.level for result in report.results], ["resolved", "none", "none", "resolved"])
    check("the report counts by status", report.summary(), {"verified": 2, "unverified": 1, "failed": 1, "total": 4})
    check("the failed block is listed", [result.hash for result in report.failed], ["b"])

    executor = FakeExecutor()
    ran = await service.verify_blocks(blocks, ENV, executor)
    check("only a resolved block that is more than a fragment is run", executor.ran, [GOOD])
    check("it reaches `ran`", [result.level for result in ran.results], ["ran", "none", "none", "resolved"])

    broken = await service.verify_blocks(blocks[:1], ENV, FakeExecutor(ok = False))
    check("a run that fails is a failed block with the end of its output", (broken.results[0].status, "NameError" in broken.results[0].issues[0]), ("failed", True))
    down = await service.verify_blocks(blocks[:1], ENV, FakeExecutor(raises = True))
    check("an executor that raises is a failed block, not a failed report", (down.results[0].status, "could not finish" in down.results[0].issues[0]), ("failed", True))

    text = "```python\nx = 1\n```\n\n```python\ndef f(:\n```\n"
    drafted = await service.verify_text(text, ENV)
    check("a draft's fences are verified, named by the hash of their code", (drafted.summary()["failed"], len({result.hash for result in drafted.results})), (1, 2))

    built = domains.dd.pipeline.ledger.domain.build([("a.md", domains.dd.ingestion.vault.domain.sentinelize_doc(f"```python\n{GOOD}```\n")[1])])
    from_ledger = await service.verify_ledger(built, ENV)
    check("the ledger's must-teach blocks are verified", from_ledger.summary(), {"verified": 1, "unverified": 0, "failed": 0, "total": 1})


async def test_subprocess_executor() -> None:
    run = service.SubprocessExecutor()
    ok = await run.run("print('hello')\n", lang = "python", timeout_s = 10)
    check("a run that ends cleanly is ok, with its output", (ok.ok, ok.stdout.strip()), (True, "hello"))
    bad = await run.run("raise ValueError('boom')\n", lang = "python", timeout_s = 10)
    check("a run that raises fails with the end of its error", (bad.ok, "ValueError: boom" in bad.stderr), (False, True))
    slow = await run.run("while True:\n    pass\n", lang = "python", timeout_s = 1)
    check("a run that never ends is stopped at the timeout", (slow.ok, "timed out" in slow.stderr), (False, True))
    env = await run.run("import os\nprint(sorted(os.environ))\n", lang = "python", timeout_s = 10)
    check("the child sees a stripped environment", "REDIS_HOST" in env.stdout or "AWS_ACCESS_KEY_ID" in env.stdout, False)
    cwd = await run.run("import os\nprint(os.listdir('.'))\n", lang = "python", timeout_s = 10)
    check("it runs in an empty directory holding only its own file", cwd.stdout.strip(), "['main.py']")
    other = await run.run("echo hi", lang = "bash", timeout_s = 10)
    check("only Python is run", (other.ok, "only Python" in other.stderr), (False, True))
    check("the executor is off by default", service.executor(), None)
    params_module = verify.params
    params_module.RUN_ENABLED = True
    try:
        check("…and is the subprocess one when running code is enabled", isinstance(service.executor(), service.SubprocessExecutor), True)
    finally:
        params_module.RUN_ENABLED = False
    report = await service.verify_blocks([("a", "python", "import json\nprint(json.dumps([1, 2]))\nprint('done')\n")], ENV, run)
    check("a block that resolves and runs reaches `ran`", (report.results[0].level, report.results[0].status), ("ran", "verified"))


async def main() -> None:
    test_domain()
    test_fences()
    await test_service()
    await test_subprocess_executor()
    print("\nverify: all checks passed")


if __name__ == "__main__":
    asyncio.run(main())
