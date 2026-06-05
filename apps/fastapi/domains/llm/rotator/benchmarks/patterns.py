from __future__ import annotations

import re


# Provider prefix — strip "meta/", "moonshotai/", "models/", etc.
PROVIDER_PREFIX_RE = re.compile(r"^[^/]+/")

# Whitespace → dash. OpenLM HTML cells render some entries with spaces
# ("Mistral Large 3"); discovery uses dashes. Align before suffix-stripping.
WHITESPACE_RE = re.compile(r"\s+")

# Collapse repeated dashes after suffix strips leave double dashes behind.
DASH_RUN_RE = re.compile(r"-+")

# First numeric value in a cell — handles '1467', '87.1', '-12.3', '5.1+'.
CELL_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")
