from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


KeySource = Literal["user", "env"]


@dataclass(frozen = True, slots = True)
class KeyStatus:
    """`last4` is the most that may ever leave the process."""
    has_key: bool
    source:  KeySource | None
    last4:   str | None

    @classmethod
    def unset(cls) -> "KeyStatus":
        return cls(has_key = False, source = None, last4 = None)
