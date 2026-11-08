"""Checking code without running it, pure: does it parse, do its imports resolve, is a configuration file valid. A block is never called verified on
the strength of a check that does not apply to it: a language with no check ends `unverified`, and a run is what lifts a block to `ran`."""
from __future__ import annotations
import domains
from . import entities, params

import importlib.util
import json
import sys
import tomllib


def module_state(name: str, environment: entities.Environment) -> str:
    """`stdlib`, `known` (the docs' own framework), `installed` in the worker, or `missing`."""
    if name in sys.stdlib_module_names:
        return "stdlib"
    if name in environment.known:
        return "known"
    try:
        return "installed" if importlib.util.find_spec(name) is not None else "missing"
    except (ImportError, ValueError):
        return "missing"


def check_python(body: str, environment: entities.Environment) -> tuple[str, bool | None, tuple[str, ...]]:
    """`(level, ok, issues)` of Python code. A session with prompts is not source; code that does not parse is wrong; an import nothing provides
    leaves the block `parsed` but unchecked (it may be a library the worker does not have)."""
    parses, imports, _defines = domains.dd.pipeline.ledger.domain.python_facts(body)
    if parses is None:
        return "none", None, ("an interactive session, not source",)
    if not parses:
        return "none", False, ("does not parse",)
    missing = [name for name in imports if module_state(name, environment) == "missing"]
    if missing:
        return "parsed", None, (f"cannot check the import of {', '.join(missing)}: not in the docs' framework and not installed",)
    return "resolved", True, ()


def check_config(lang: str, body: str) -> tuple[str, bool | None, tuple[str, ...]]:
    """A configuration file parses or it does not."""
    kind = params.CONFIG_PARSERS.get(lang)
    try:
        if kind == "json":
            json.loads(body)
        elif kind == "tomllib":
            tomllib.loads(body)
        elif kind == "yaml":
            import yaml
            yaml.safe_load(body)
        else:
            return "none", None, ("no check for this language",)
    except Exception as error:
        return "none", False, (f"does not parse: {type(error).__name__}: {str(error)[:120]}",)
    return "parsed", True, ()


def verify_block(digest: str, lang: str, body: str, environment: entities.Environment) -> entities.Result:
    """What the static checks say about one block."""
    if lang == "python":
        level, ok, issues = check_python(body, environment)
    elif lang in params.CONFIG_PARSERS:
        level, ok, issues = check_config(lang, body)
    else:
        level, ok, issues = "none", None, ("no check for this language",)
    return entities.Result(hash = digest, lang = lang, level = level, ok = ok, issues = issues)


def extract_fences(markdown: str) -> list[tuple[str, str]]:
    """`(language, code)` of every fenced block in a text, in order: what a writer wrote into a draft, which the vault never saw."""
    ledger = domains.dd.pipeline.ledger.domain
    fences: list[tuple[str, str]] = []
    opening: tuple[str, str, list[str]] | None = None
    for line in markdown.splitlines():
        match = ledger.FENCE_LINE_RE.match(line)
        if opening is None and match:
            info = line.strip().lstrip("`~").strip()
            opening = (match.group(1)[0], ledger.language(info.split()[0]) if info else "", [])
        elif opening is not None and match and match.group(1)[0] == opening[0] and not line.strip().strip("`~"):
            fences.append((opening[1], "\n".join(opening[2])))
            opening = None
        elif opening is not None:
            opening[2].append(line)
    return fences


def after_run(result: entities.Result, run: entities.RunResult) -> entities.Result:
    """The block's result once it has been run: it reaches `ran`, or it fails with the end of what it printed."""
    if run.ok:
        return entities.Result(hash = result.hash, lang = result.lang, level = "ran", ok = True, issues = ())
    tail = (run.stderr or run.stdout)[-params.MAX_OUTPUT_CHARS:].strip()
    return entities.Result(hash = result.hash, lang = result.lang, level = result.level, ok = False, issues = (f"the run failed: {tail}",))
