"""What the synth check tools take from the node."""
from __future__ import annotations

import collections.abc


# check(draft) -> the issues the stage's validators find in the draft (none when it is valid).
Check = collections.abc.Callable[[dict], collections.abc.Awaitable[list[str]]]
