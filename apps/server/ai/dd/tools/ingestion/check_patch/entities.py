"""What the repairer's patch check takes from the node."""
from __future__ import annotations

import collections.abc
import typing


# check(markdown, source_sha256, assessment, fields) -> "" when the patch would be applied, else why not (with what to change).
Check = collections.abc.Callable[[str, str, dict[str, typing.Any], dict[str, typing.Any]], str]
