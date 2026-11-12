"""What a documentation export writes that a later stage converts. The auditor's prompt lists these as never reportable (`render_conventions`),
and the audit drops a finding that quotes one (`EXPORT_CONVENTION_RE`); a test checks every example below against the pattern."""
from __future__ import annotations

from collections import Counter
import html
import html.parser
import re
import shlex


# (what it is, examples of it). The prompt is rendered from this table, so the list the model reads cannot drift from the filter.
EXPORT_CONVENTIONS = (
    (
        "JSX components of MDX sites",
        ("<Note>", "<Info>", "<Warning>", "<Tip>", "<Check>", "<Tabs>", "<Tab>", "<Card>", "<CardGroup>", "<Steps>", "<Accordion>"),
    ),
    ("empty media placeholders and media tags", ('<img alt="" />', "<iframe>", "<video>")),
    ("attributes on a code fence's opening line", ("```python theme={null}",)),
    (
        "block-quote forms of tabs, cards, steps and callouts",
        ("> [tab] Name", "> [card] Title", "> [step] Name", "> [!NOTE]", "> [!WARNING]", "> > [!TIP]"),
    ),
    ("GitHub-flavoured table alignment rows, `:-` and `-` alike", ("| :- | :- |", "| :-: | -: |", "| - | - |")),
    (
        "GitBook template blocks and embeds",
        (
            "{% stepper %}", "{% step %}", "{% tab title=\"Python\" %}",
            "{% columns %}", "{% column width=\"50%\" %}",
            "{% file src=\"/files/example\" %}", "{% code overflow=\"wrap\" %}",
            "{% embed url=\"https://example.com\" %}",
        ),
    ),
)
EXPORT_CONVENTION_RE = re.compile(
    r"\A\s*(?:<[A-Z][A-Za-z]*\b|<(?:img|iframe|video)\b|(?:`{3,}|~{3,})[^\n]*\btheme="
    r"|>(?:[ \t]*>)*[ \t]*\[(?i:tab|card|step|accordion|!(?:note|tip|warning|caution|important|info|check))\])"
)
TABLE_DELIMITER_RE = re.compile(r"\|?[ \t]*:?-+:?[ \t]*(?:\|[ \t]*:?-+:?[ \t]*)*\|?")
GITBOOK_COMPONENT_NAMES = frozenset({
    "stepper", "step", "tabs", "tab", "columns", "column", "file", "code",
    "embed", "content-ref", "hint", "update", "callout", "expand", "details",
})
GITBOOK_COMPONENT_TAG_RE = re.compile(
    r"\{%\s*(?P<end>end)?(?P<name>stepper|step|tabs?|columns?|column|file|code|embed|content-ref|hint|update|callout|expand|details)\b[^%]*%\}",
    re.IGNORECASE,
)
HTML_TAG_RE = re.compile(r"</?([A-Za-z][A-Za-z0-9:-]*)\b[^<>]*?/?>")
HTML_TAG_ONLY_RE = re.compile(
    r"\A\s*</?[A-Za-z][A-Za-z0-9:-]*(?:\s+[A-Za-z_:][A-Za-z0-9_.:-]*(?:\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s\"'<>`]+))?)*\s*/?>\s*\Z",
    re.DOTALL,
)
HTML_NUMERIC_ENTITY_RE = re.compile(r"&#(?:[0-9]{1,7}|[xX][0-9a-fA-F]{1,6});")
ESCAPED_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9]+(?:\\_[A-Za-z0-9]+)+")
VOID_HTML_TAGS = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"})


class _HTMLTagCounter(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.open_tags: Counter[str] = Counter()
        self.close_tags: Counter[str] = Counter()

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag not in VOID_HTML_TAGS:
            self.open_tags[tag] += 1

    def handle_endtag(self, tag: str) -> None:
        if tag not in VOID_HTML_TAGS:
            self.close_tags[tag] += 1


def table_cells(row: str) -> int:
    """How many cells a table row has: the segments between its unescaped pipes outside code spans, not counting the edges."""
    cells = 1
    in_code = False
    escaped = False
    for char in row.strip():
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == "`":
            in_code = not in_code
        elif char == "|" and not in_code:
            cells += 1
    stripped = row.strip()
    return cells - (stripped.startswith("|") + (stripped.endswith("|") and not stripped.endswith("\\|")))


def is_sound_table_head(evidence: str) -> bool:
    """Whether evidence contains a valid GFM header and delimiter row.

    Body rows may omit trailing cells; GFM renders the missing cells as empty.
    """
    lines = [line.strip() for line in evidence.strip().splitlines() if line.strip()]
    if not lines or not lines[0].startswith("|"):
        return False
    at = next((index for index in (0, 1) if index < len(lines) and TABLE_DELIMITER_RE.fullmatch(lines[index]) and "|" in lines[index]), None)
    if at is None:
        return False
    width = table_cells(lines[at])
    header_is_well_formed = at == 0 or table_cells(lines[0]) == width
    return header_is_well_formed and all(
        table_cells(line) <= width
        for index, line in enumerate(lines)
        if index != at
    )


def is_valid_table_row_context(markdown: str, evidence: str) -> bool:
    """Whether a cited row is part of a valid GFM table, including short body rows."""
    cited = evidence.strip()
    if not cited.startswith("|") or "\n" in cited:
        return False
    lines = markdown.splitlines()
    for index, line in enumerate(lines):
        if line.strip() != cited:
            continue
        for delimiter_index in (index - 1, index + 1):
            if not (
                0 <= delimiter_index < len(lines)
                and "|" in lines[delimiter_index]
                and TABLE_DELIMITER_RE.fullmatch(lines[delimiter_index].strip())
            ):
                continue
            width = table_cells(lines[delimiter_index])
            header_index = delimiter_index - 1
            if (
                header_index >= 0
                and "|" in lines[header_index]
                and table_cells(lines[header_index]) == width
                and table_cells(line) <= width
            ):
                return True
    return False


def contains_valid_escaped_ampersand(evidence: str) -> bool:
    """Whether evidence includes Markdown's valid backslash escape for an ampersand."""
    return r"\&" in evidence


def contains_escaped_identifier(evidence: str) -> bool:
    """Whether evidence includes an identifier whose escaped underscores render literally."""
    return ESCAPED_IDENTIFIER_RE.search(evidence) is not None


def evidence_is_valid_atx_heading(markdown: str, evidence: str) -> bool:
    """Whether evidence occurs on a syntactically valid ATX heading line."""
    for line in markdown.splitlines():
        if evidence not in line:
            continue
        match = re.match(r"^ {0,3}(#{1,6})(?:[ \t]+(.*)|[ \t]*)$", line)
        if match is None:
            continue
        content = (match.group(2) or "").strip()
        if content and not re.fullmatch(r"#+", content):
            return True
    return False


def is_valid_blockquote(evidence: str) -> bool:
    """Whether the cited lines use valid blockquote markers, regardless of editorial intent."""
    lines = [line for line in evidence.splitlines() if line.strip()]
    return bool(lines) and all(re.match(r"^ {0,3}>", line) for line in lines)


def is_package_manager_alternative(evidence: str) -> bool:
    """Whether evidence repeats identical package requirements for pip and uv installation options."""
    blocks = re.findall(r"```[^\n]*\n(.*?)\n```", evidence, re.DOTALL)
    installs: list[tuple[str, tuple[str, ...]]] = []
    for block in blocks:
        commands = [line.strip() for line in block.splitlines() if line.strip()]
        if len(commands) != 1:
            continue
        try:
            parts = shlex.split(commands[0])
        except ValueError:
            continue
        if len(parts) < 3 or parts[:2] not in (["pip", "install"], ["pip3", "install"], ["uv", "add"]):
            continue
        installs.append(("pip" if parts[0] in {"pip", "pip3"} else "uv", tuple(parts[2:])))

    managers = {manager for manager, _requirements in installs}
    requirement_sets = {requirements for _manager, requirements in installs}
    return len(installs) >= 2 and managers == {"pip", "uv"} and len(requirement_sets) == 1


def is_model_configuration_code_variant(evidence: str) -> bool:
    """Whether fenced examples are otherwise identical and differ only by model configuration."""
    blocks = re.findall(r"```[^\n]*\n(.*?)\n```", evidence, re.DOTALL)
    model_line = re.compile(r"(?m)^(\s*model\s*=\s*)(?:\"([^\"]*)\"|'([^']*)')")
    values: list[str] = []
    normalized: list[str] = []
    for block in blocks:
        matches = list(model_line.finditer(block))
        if len(matches) != 1:
            return False
        values.append(matches[0].group(2) or matches[0].group(3) or "")
        normalized.append(model_line.sub(r'\1"<MODEL>"', block))
    return len(blocks) >= 2 and len(set(values)) > 1 and len(set(normalized)) == 1


def is_valid_escaped_identifier(evidence: str) -> bool:
    """Whether evidence is just an identifier whose underscores are correctly backslash-escaped in Markdown."""
    return ESCAPED_IDENTIFIER_RE.fullmatch(evidence.strip()) is not None


def is_valid_numeric_entity_text(evidence: str) -> bool:
    """Whether an alleged encoding defect is valid HTML numeric character-reference text."""
    if not HTML_NUMERIC_ENTITY_RE.search(evidence):
        return False
    decoded = html.unescape(evidence)
    return decoded != evidence and "\ufffd" not in decoded


def is_structural_export_markup_only(evidence: str) -> bool:
    """Whether repeated evidence contains only HTML or GitBook block markers, with no reader-facing content."""
    lines = [line.strip() for line in evidence.splitlines() if line.strip()]
    return bool(lines) and all(
        GITBOOK_COMPONENT_TAG_RE.fullmatch(line) is not None
        or HTML_TAG_ONLY_RE.fullmatch(line) is not None
        for line in lines
    )


def is_export_convention(evidence: str, *, markdown: str = "") -> bool:
    """Whether evidence is valid site-export syntax or raw HTML, not a page defect."""
    if (
        EXPORT_CONVENTION_RE.match(evidence)
        or is_sound_table_head(evidence)
        or (markdown and is_valid_table_row_context(markdown, evidence))
    ):
        return True
    if GITBOOK_COMPONENT_TAG_RE.search(evidence) or HTML_TAG_ONLY_RE.fullmatch(evidence):
        return True
    if not markdown:
        return False
    evidence_tags = {match.group(1).lower() for match in HTML_TAG_RE.finditer(evidence)}
    if not evidence_tags:
        return False
    counts = _HTMLTagCounter()
    try:
        counts.feed(markdown)
        counts.close()
    except Exception:
        return False
    return all(
        tag in VOID_HTML_TAGS
        or (counts.open_tags[tag] > 0 and counts.open_tags[tag] == counts.close_tags[tag])
        for tag in evidence_tags
    )


def is_inside_export_component(markdown: str, evidence: str) -> bool:
    """Whether a heading or syntax finding sits inside a balanced GitBook component block."""
    start = markdown.find(evidence)
    if start < 0:
        return False
    stack: list[tuple[str, int]] = []
    spans: list[tuple[int, int]] = []
    for match in GITBOOK_COMPONENT_TAG_RE.finditer(markdown):
        name = match.group("name").lower()
        if name not in GITBOOK_COMPONENT_NAMES:
            continue
        if match.group("end"):
            for index in range(len(stack) - 1, -1, -1):
                if stack[index][0] == name:
                    _opened_name, opened_at = stack.pop(index)
                    spans.append((opened_at, match.start()))
                    del stack[index:]
                    break
        else:
            stack.append((name, match.end()))
    return any(opened_at <= start < closed_at for opened_at, closed_at in spans)


def render_conventions() -> str:
    """The table as the bullet list the auditor's prompt carries."""
    def code(text: str) -> str:
        return f"`` {text} ``" if "`" in text else f"`{text}`"

    return "\n".join(
        f"- {what}: {', '.join(code(example) for example in examples)}"
        for what, examples in EXPORT_CONVENTIONS
    )
