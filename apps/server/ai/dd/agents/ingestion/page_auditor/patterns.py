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
)
EXPORT_CONVENTION_RE = re.compile(r"\A\s*(?:<[A-Z][A-Za-z]*\b|<(?:img|iframe|video)\b|(?:`{3,}|~{3,})[^\n]*\btheme=)")


def render_conventions() -> str:
    """The table as the bullet list the auditor's prompt carries."""
    def code(text: str) -> str:
        return f"`` {text} ``" if "`" in text else f"`{text}`"

    return "\n".join(
        f"- {what}: {', '.join(code(example) for example in examples)}"
        for what, examples in EXPORT_CONVENTIONS
    )
