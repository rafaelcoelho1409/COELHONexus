from __future__ import annotations

import re


# The runs of whitespace and underscores in a file name, which become single spaces in the title taken from it.
FILENAME_NOISE_RE = re.compile(r"[\s_]+")


# The `.pdf` that ends a file name (any case), left out of the title taken from it.
FILENAME_SUFFIX_RE = re.compile(r"\.pdf$", re.IGNORECASE)
