"""What a documentation export writes that a later stage converts. The auditor's prompt lists these as never reportable (`render_conventions`),
and the audit drops a finding that quotes one (`EXPORT_CONVENTION_RE`); a test checks every example below against the pattern."""
from __future__ import annotations

from collections import Counter
from html.parser import HTMLParser
import re


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
VOID_HTML_TAGS = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"})


class _HTMLTagCounter(HTMLParser):
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
    """Whether the evidence is a table head that is sound GitHub-flavoured Markdown: an optional header row, then a delimiter row (`:-`, `-:`,
    `:-:` or plain dashes), with every row after it as wide as the delimiter row. A finding that quotes such a table calls valid syntax broken."""
    lines = [line.strip() for line in evidence.strip().splitlines() if line.strip()]
    if not lines or not lines[0].startswith("|"):
        return False
    at = next((index for index in (0, 1) if index < len(lines) and TABLE_DELIMITER_RE.fullmatch(lines[index]) and "|" in lines[index]), None)
    if at is None:
        return False
    width = table_cells(lines[at])
    return all(table_cells(line) == width for index, line in enumerate(lines) if index != at)


def is_export_convention(evidence: str, *, markdown: str = "") -> bool:
    """Whether evidence is valid site-export syntax or raw HTML, not a page defect."""
    if EXPORT_CONVENTION_RE.match(evidence) or is_sound_table_head(evidence):
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
