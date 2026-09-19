"""HTML → Markdown extraction shared by tier2/tier3/tier4: strip chrome, normalize KaTeX/MathJax math to $…$/$$…$$ delimiters, convert with markdownify."""
from __future__ import annotations

from . import domain, params


__all__ = ["domain", "params"]
