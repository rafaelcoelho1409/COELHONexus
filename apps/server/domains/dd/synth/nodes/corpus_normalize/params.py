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
    # react.dev's own components (Tier 2 pages carry them raw): callouts and sections wrap prose, `Sandpack` wraps the
    # example's code fences, `CodeStep` marks a word inline, `InlineToc` and the diagrams are page furniture.
    "Intro", "Pitfall", "DeepDive", "Recap", "YouWillLearn", "Challenges", "Solution", "Recipes",
    "Sandpack", "SandpackWithHTMLOutput", "CodeStep", "LearnMore", "InlineToc", "Illustration", "IllustrationBlock",
    "Diagram", "DiagramGroup", "TerminalBlock", "ConsoleBlock", "ConsoleBlockMulti", "ConsoleLogLine", "Deprecated",
    "RSC", "Math", "MathI",
    # …and the ones the rest of react.dev's reference uses: status callouts and badges, wide-figure wrappers.
    "Experimental", "ExperimentalBadge", "Canary", "CanaryBadge", "Wip", "FullWidth", "CodeDiagram",
)

# Only the tail of a page is searched for the host's footer for agents.
AGENT_FOOTER_TAIL_CHARS = 800


# A multi-line `<video>` / `<audio>` element is looked for this far down from its opening line.
MEDIA_MAX_LINES = 12


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
