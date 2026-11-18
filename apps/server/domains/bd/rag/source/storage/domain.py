"""Pure parsing and validation for pinned processed-book source artifacts."""
from __future__ import annotations

import json
from typing import Any


def source_rows(content: str) -> list[dict[str, Any]]:
    rows = []
    for line in content.splitlines():
        if line.strip():
            row = json.loads(line)
            if not isinstance(row, dict) or not isinstance(row.get("content"), str):
                raise ValueError("the pinned source artifact contains a malformed page record")
            rows.append(row)
    if not rows:
        raise ValueError("the pinned source artifact contains no pages")
    return rows
