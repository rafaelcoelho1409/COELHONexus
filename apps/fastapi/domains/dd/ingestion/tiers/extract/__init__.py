"""HTML → Markdown extraction shared by tier2/tier3/tier4: strip chrome, normalize KaTeX/MathJax math to $…$/$$…$$ delimiters, convert with markdownify."""
from .domain import extract_title, find_content_root, html_to_markdown, strip_chrome


__all__ = [
    "extract_title",
    "find_content_root",
    "html_to_markdown",
    "strip_chrome",
]
