"""What the repairer's patch check takes from the node."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any


# check(markdown, source_sha256, assessment, fields) -> "" when the patch would be applied, else why not (with what to change).
Check = Callable[[str, str, dict[str, Any], dict[str, Any]], str]
