"""Export-only syntax examples shown in the auditor prompt and enforced by page auditing."""
from __future__ import annotations


# (what it is, examples of it). Keep the prompt's examples in sync with the page-audit rules.
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
