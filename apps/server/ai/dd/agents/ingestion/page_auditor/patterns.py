"""What a documentation export writes that a later stage converts. The auditor's prompt lists these as never reportable (`render_conventions`),
and the audit drops a finding that quotes one (`EXPORT_CONVENTION_RE`); a test checks every example below against the pattern."""
from __future__ import annotations

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
)
EXPORT_CONVENTION_RE = re.compile(
    r"\A\s*(?:<[A-Z][A-Za-z]*\b|<(?:img|iframe|video)\b|(?:`{3,}|~{3,})[^\n]*\btheme="
    r"|>(?:[ \t]*>)*[ \t]*\[(?i:tab|card|step|accordion|!(?:note|tip|warning|caution|important|info|check))\])"
)
TABLE_DELIMITER_RE = re.compile(r"\|?[ \t]*:?-+:?[ \t]*(?:\|[ \t]*:?-+:?[ \t]*)*\|?")


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


def is_export_convention(evidence: str) -> bool:
    """Whether the evidence is text a documentation export writes on purpose (the table above), not a defect of the page."""
    return bool(EXPORT_CONVENTION_RE.match(evidence)) or is_sound_table_head(evidence)


def render_conventions() -> str:
    """The table as the bullet list the auditor's prompt carries."""
    def code(text: str) -> str:
        return f"`` {text} ``" if "`" in text else f"`{text}`"

    return "\n".join(
        f"- {what}: {', '.join(code(example) for example in examples)}"
        for what, examples in EXPORT_CONVENTIONS
    )
