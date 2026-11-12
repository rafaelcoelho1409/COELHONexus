"""Reading the page and its audit back from the agent's virtual filesystem."""
from __future__ import annotations

import typing


def file_text(files: dict[str, typing.Any], path: str) -> str:
    content = (files.get(path) or {}).get("content", "")
    return content if isinstance(content, str) else "\n".join(content)
