"""The code ledger, pure: which code blocks a chapter's pages carry, which of them are variants of one example, which variant to teach, and how long
a chapter takes to read. Nothing here reads storage or calls a model, so a ledger is the same for the same pages."""
from __future__ import annotations
import domains
from . import entities, params, patterns

import ast
import keyword
import re
import textwrap

# Python's names that say nothing about what an example does.
COMMON_NAMES = frozenset({"self", "cls", "print", "none", "true", "false", "args", "kwargs", "str", "int", "float", "dict", "list", "len", "range", "type"})
LANG_FAMILY = {"py": "python", "python3": "python", "py3": "python", "js": "javascript", "node": "javascript", "ts": "typescript", "": "python"}
FENCE_LINE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
CLOSING_FENCE_RE = re.compile(r"\s*(?:`{3,}|~{3,})\s*")
QUOTE_PREFIX_RE = re.compile(r"^\s*>\s?")
DEFINE_RE = re.compile(r"^\s*(?:async\s+def|def|class|function|fn|func|interface|type)\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE)


def fence_body(fence_text: str) -> str:
    """The code of a fenced block: the lines between its fences, with a block-quote prefix taken off when every line has one."""
    lines = fence_text.split("\n")
    content = [line for line in lines if line.strip()]
    if content and all(QUOTE_PREFIX_RE.match(line) for line in content):
        lines = [QUOTE_PREFIX_RE.sub("", line, count = 1) for line in lines]
    lines = lines[1:]
    if lines and CLOSING_FENCE_RE.fullmatch(lines[-1]):
        lines = lines[:-1]
    return textwrap.dedent("\n".join(lines)).strip("\n")


def language(lang: str) -> str:
    """The language a block is written in, with the usual aliases folded together."""
    token = (lang or "").strip().lower()
    return LANG_FAMILY.get(token, token)


def role_of(lang: str, body: str) -> str:
    """`setup` for an install line, `config` for a configuration file, `output` for printed text, `code` for anything else."""
    if lang in params.SHELL_LANGS:
        first = next((line for line in body.splitlines() if line.strip() and not line.strip().startswith("#")), "")
        return "setup" if patterns.INSTALL_RE.match(first) else "code"
    if lang in params.CONFIG_LANGS:
        return "config"
    if lang in params.OUTPUT_LANGS:
        return "output"
    return "code"


def python_facts(body: str) -> tuple[bool | None, tuple[str, ...], tuple[str, ...]]:
    """`(parses, imports, defines)` of a Python block. A session with prompts (`>>>`) is not source and has no verdict."""
    if patterns.PROMPT_RE.search(body):
        return None, (), ()
    try:
        tree = ast.parse(body)
    except (SyntaxError, ValueError):
        return False, (), tuple(dict.fromkeys(DEFINE_RE.findall(body)))
    imports: list[str] = []
    defines: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            imports.append(node.module.split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defines.append(node.name)
    return True, tuple(dict.fromkeys(imports)), tuple(dict.fromkeys(defines))


def identifiers(body: str) -> frozenset[str]:
    """The names a block uses, the way two variants of one example are told from two different examples."""
    names = (name for name in patterns.IDENTIFIER_RE.findall(body) if len(name) >= params.MIN_IDENTIFIER_CHARS)
    return frozenset(name for name in names if not keyword.iskeyword(name) and name.lower() not in COMMON_NAMES)


def similarity(first: frozenset[str], second: frozenset[str]) -> float:
    union = first | second
    return len(first & second) / len(union) if union else 0.0


def score_block(*, lines: int, parses: bool | None, imports: tuple[str, ...], defines: tuple[str, ...], deprecated: bool, extra_pages: int) -> float:
    """How good a block is as the one to teach: it parses, brings its imports, is a size a step can hold, defines something, appears on several pages
    and is not marked as the old way."""
    score = 0.0
    if parses:
        score += params.SCORE_PARSES
    if imports:
        score += params.SCORE_IMPORTS
    low, high = params.SWEET_SPOT_LINES
    if low <= lines <= high:
        score += params.SCORE_SWEET_SPOT
    elif high < lines <= params.LONG_LINES:
        score += params.SCORE_LONG
    if lines <= params.TRIVIAL_LINES:
        score += params.SCORE_TRIVIAL
    if defines:
        score += params.SCORE_DEFINES
    if imports and lines >= low:
        score += params.SCORE_COMPLETE
    if deprecated:
        score += params.SCORE_DEPRECATED
    score += min(params.SCORE_EXTRA_PAGES_CAP, params.SCORE_PER_EXTRA_PAGE * extra_pages)
    return round(score, 3)


def make_block(entry: domains.dd.ingestion.vault.schemas.VaultEntry, pages: tuple[str, ...], order: int) -> entities.Block:
    lang = language(entry.lang)
    body = fence_body(entry.fence_text)
    lines = len([line for line in body.splitlines() if line.strip()])
    if lang == "python":
        parses, imports, defines = python_facts(body)
    else:
        parses, imports, defines = None, (), tuple(dict.fromkeys(DEFINE_RE.findall(body)))
    role = role_of(lang, body)
    if role == "code" and not entry.lang and parses is not True:
        role = "output"
    return entities.Block(
        hash = entry.hash,
        lang = lang,
        body = body,
        lines = lines,
        pages = pages,
        order = order,
        role = role,
        parses = parses,
        imports = imports,
        defines = defines,
        score = score_block(
            lines = lines, parses = parses, imports = imports, defines = defines,
            deprecated = bool(patterns.DEPRECATED_RE.search(body)), extra_pages = len(pages) - 1,
        ),
    )


def group_variants(blocks: list[entities.Block]) -> list[list[entities.Block]]:
    """Blocks of one role and language whose identifiers overlap enough with a family's best block are variants of that example. Names that most blocks
    of a large ledger share (`create_deep_agent` in every Deep Agents example) say nothing about which example a block is, so they are left out of the
    comparison; and a block is compared with the family's seed, never with its other members, so a chain of near-neighbours cannot merge two examples."""
    names = [identifiers(block.body) for block in blocks]
    if len(blocks) >= params.MIN_BLOCKS_FOR_COMMON:
        counts: dict[str, int] = {}
        for found in names:
            for name in found:
                counts[name] = counts.get(name, 0) + 1
        common = {name for name, count in counts.items() if count / len(blocks) > params.COMMON_NAME_RATIO}
        names = [found - common for found in names]
    seeds: list[int] = []
    members: dict[int, list[int]] = {}
    for index in sorted(range(len(blocks)), key = lambda i: (-blocks[i].score, blocks[i].order)):
        for seed in seeds:
            same_kind = blocks[index].role == blocks[seed].role and blocks[index].lang == blocks[seed].lang
            if same_kind and similarity(names[index], names[seed]) >= params.VARIANT_JACCARD:
                members[seed].append(index)
                break
        else:
            seeds.append(index)
            members[index] = [index]
    return [[blocks[index] for index in members[seed]] for seed in seeds]


def build(pages: list[tuple[str, dict[str, domains.dd.ingestion.vault.schemas.VaultEntry]]]) -> entities.Ledger:
    """The ledger of a chapter from its pages, in reading order: each page is `(page key, its vault entries in document order)`."""
    entries: dict[str, domains.dd.ingestion.vault.schemas.VaultEntry] = {}
    where: dict[str, list[str]] = {}
    for key, vault in pages:
        for digest, entry in vault.items():
            entries.setdefault(digest, entry)
            if key not in where.setdefault(digest, []):
                where[digest].append(key)
    blocks = {digest: make_block(entry, tuple(where[digest]), order) for order, (digest, entry) in enumerate(entries.items())}
    families = []
    for group in group_variants(list(blocks.values())):
        ranked = sorted(group, key = lambda block: (-block.score, block.hash))
        families.append((min(block.order for block in group), entities.Family(
            hashes = tuple(block.hash for block in ranked),
            role = ranked[0].role,
            pages = tuple(dict.fromkeys(page for block in group for page in block.pages)),
        )))
    families.sort(key = lambda item: item[0])
    ordered = tuple(family for _, family in families)
    must_teach = tuple(
        family.canonical for family in ordered
        if family.role == "code" and blocks[family.canonical].lines > params.TRIVIAL_LINES and blocks[family.canonical].score >= params.MIN_TEACH_SCORE
    )
    setup = tuple(family.canonical for family in ordered if family.role == "setup")
    return entities.Ledger(blocks = blocks, families = ordered, must_teach = must_teach, setup = setup, pages = tuple(key for key, _ in pages))


def uncovered(ledger: entities.Ledger, used: set[str]) -> list[str]:
    """The must-teach blocks no step of the chapter uses, in teaching order. A variant of a family counts as using its canonical block."""
    variant_of = {digest: family.canonical for family in ledger.families for digest in family.hashes}
    taught = {variant_of.get(digest, digest) for digest in used}
    return [digest for digest in ledger.must_teach if digest not in taught]


def summary(ledger: entities.Ledger, limit: int = params.SUMMARY_MAX_BLOCKS) -> list[dict]:
    """The ledger as the compact list an agent reads: the blocks to teach, in order, with what each shows, then the setup lines."""
    rows = []
    families = {family.canonical: family for family in ledger.families}
    for digest in (*ledger.must_teach, *ledger.setup)[:limit]:
        block = ledger.blocks[digest]
        rows.append({
            "hash": digest,
            "role": block.role,
            "lang": block.lang,
            "lines": block.lines,
            "defines": list(block.defines[:4]),
            "imports": list(block.imports[:4]),
            "pages": len(families[digest].pages),
            "variants": len(families[digest].hashes) - 1,
            "preview": "\n".join([line for line in block.body.splitlines() if line.strip()][:params.SUMMARY_PREVIEW_LINES]),
        })
    return rows


def page_stats(markdown: str) -> tuple[int, int]:
    """`(words of prose, lines of code)` of a page: what is between fences is code, the rest is read as prose."""
    words = 0
    code_lines = 0
    fence = ""
    for line in markdown.splitlines():
        match = FENCE_LINE_RE.match(line)
        if match:
            marker = match.group(1)[0]
            fence = "" if fence == marker else (fence or marker)
            continue
        if fence:
            code_lines += 1 if line.strip() else 0
        else:
            words += len(line.split())
    return words, code_lines


def reading_minutes(words: int, code_lines: int) -> float:
    """Minutes to read prose of `words` and follow `code_lines` of code."""
    return round(words / params.WORDS_PER_MINUTE + code_lines * params.SECONDS_PER_CODE_LINE / 60, 2)


def tutorial_minutes(ledger: entities.Ledger) -> float:
    """Minutes the tutorial built from a ledger takes to read: a step of explanation and the code for each best example, an introduction, and a capstone."""
    code_lines = sum(ledger.blocks[digest].lines for digest in ledger.must_teach) + params.CAPSTONE_LINES_ESTIMATE
    words = params.WORDS_PER_STEP * len(ledger.must_teach) + params.INTRO_WORDS
    return reading_minutes(words, code_lines)


def reading_plan(chapters: list[dict]) -> dict:
    """What the plan's reading times say about its chapter count: the tutorial minutes in all, how many chapters of the target length that is, the chapters
    over the longest allowed, and the shortest ones (candidates to fold into a neighbour). A chapter with no figure (its pages could not be read) is left out."""
    timed = [chapter for chapter in chapters if chapter.get("tutorial_minutes")]
    total = round(sum(chapter["tutorial_minutes"] for chapter in timed), 1)
    return {
        "tutorial_minutes": total,
        "suggested_chapters": max(1, round(total / params.TARGET_CHAPTER_MINUTES)) if timed else 0,
        "n_chapters": len(chapters),
        "over_budget": [chapter.get("id") for chapter in timed if chapter["tutorial_minutes"] > params.MAX_CHAPTER_MINUTES],
        "short": [chapter.get("id") for chapter in timed if chapter["tutorial_minutes"] < params.TARGET_CHAPTER_MINUTES / 3],
    }


def pages_minutes(texts: list[str]) -> float:
    """Minutes to read the pages as they are, which is more than the tutorial built from them."""
    words = 0
    code_lines = 0
    for text in texts:
        page_words, page_lines = page_stats(text)
        words += page_words
        code_lines += page_lines
    return reading_minutes(words, code_lines)
