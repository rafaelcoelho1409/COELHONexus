"""HTML → Markdown extraction utilities (shared by tier2 / tier3 / tier4).

Strips navigation/footer/aside chrome with BeautifulSoup, normalizes
server-rendered math (KaTeX / MathJax) into `$…$` / `$$…$$` delimiters,
then converts the remaining body with markdownify.
"""
from .domain import extract_title, find_content_root, html_to_markdown, strip_chrome


__all__ = [
    "extract_title",
    "find_content_root",
    "html_to_markdown",
    "strip_chrome",
]
