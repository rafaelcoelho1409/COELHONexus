"""What the synth check tools take from the node."""
from __future__ import annotations

from collections.abc import Awaitable, Callable


# check(draft) -> the issues the stage's validators find in the draft (none when it is valid).
Check = Callable[[dict], Awaitable[list[str]]]
