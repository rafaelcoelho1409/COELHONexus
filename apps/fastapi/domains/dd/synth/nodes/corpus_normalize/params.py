"""corpus_normalize — tunable tag sets + HTML-entity decoding pairs."""
from __future__ import annotations


# MDX wrapper tag set (Mintlify v4, Docusaurus 3.x, Nextra 4, Starlight,
# ReadMe.io, GitBook html). Tags are STRIPPED but inner text is preserved.
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


# Mintlify code-fence attribute names per docs.mintlify.com/code (May 2026).
# Used to detect "is this info-string Mintlify-styled?" — we always reduce
# info-string to first whitespace-separated token (the lang), so this list
# is only for stat tracking + identifying the "metadata seen" case for the
# report.
FENCE_META_ATTRS = (
    "theme", "expandable", "lines", "title", "icon",
    "wrap", "highlight", "focus", "filename", "copy",
    "twoslash", "lineNumbers", "actions",
)


# Container-admonition kinds (Docusaurus / VitePress / MkDocs Material).
ADMON_KINDS = (
    "tip", "note", "warning", "caution", "info", "danger", "important",
    "details", "abstract",
)


# HTML entities we always want decoded back to their unicode form.
# Restricted list — we DON'T blanket-decode (e.g. `&copy;` is fine as-is).
ENTITY_DECODES = (
    ("&amp;",  "&"),
    ("&lt;",   "<"),
    ("&gt;",   ">"),
    ("&quot;", '"'),
    ("&#39;",  "'"),
    ("&apos;", "'"),
    ("&nbsp;", " "),
)
