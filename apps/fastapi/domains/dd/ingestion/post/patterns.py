from __future__ import annotations

import re


# Mintlify/Streamlit llms-full.txt boundary marker.
SOURCE_LINE_RE = re.compile(
    r'^Source:\s+(https?://\S+)\s*$', re.MULTILINE,
)


# Page-level H1, prepended to sub-pages so `## 2024.5.0` reads as
# `# Changelog\n\n## 2024.5.0` instead of context-free.
H1_PREFIX_RE = re.compile(r"^(#\s+[^\n]+)\n+", re.MULTILINE)
