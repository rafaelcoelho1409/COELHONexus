from __future__ import annotations

import re


PROVIDER_PREFIX_RE = re.compile(r"^[^/]+/")
WHITESPACE_RE = re.compile(r"\s+")
DASH_RUN_RE = re.compile(r"-+")
CELL_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")
