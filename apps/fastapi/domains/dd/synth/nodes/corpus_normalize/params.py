"""corpus_normalize — tunable tag sets + HTML-entity decoding pairs."""
from __future__ import annotations


# Tags are STRIPPED but inner text is preserved.
MDX_WRAPPER_TAGS = (
    # Mintlify v4
    "Tip", "Note", "Warning", "Info", "Caution", "Check", "Danger",
    "Tabs", "TabItem", "Tab", "Accordion", "AccordionGroup",
    "CodeGroup", "CodeBlock",
    "Steps", "Step", "Card", "CardGroup", "Frame",
    "ParamField", "ResponseField", "Expandable",
    "Callout", "Hint",
    # Starlight
    "Aside", "LinkCard", "FileTree",
    # Nextra 4
    "Tree",
)


# Stat tracking only — we always strip to first token regardless.
FENCE_META_ATTRS = (
    "theme", "expandable", "lines", "title", "icon",
    "wrap", "highlight", "focus", "filename", "copy",
    "twoslash", "lineNumbers", "actions",
)


ADMON_KINDS = (
    "tip", "note", "warning", "caution", "info", "danger", "important",
    "details", "abstract",
)


# Restricted decode list — not blanket (e.g. &copy; left as-is).
ENTITY_DECODES = (
    ("&amp;",  "&"),
    ("&lt;",   "<"),
    ("&gt;",   ">"),
    ("&quot;", '"'),
    ("&#39;",  "'"),
    ("&apos;", "'"),
    ("&nbsp;", " "),
)
