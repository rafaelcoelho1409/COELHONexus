"""Regular expressions used by BD Distill synthesis rules."""
from __future__ import annotations

import re


NUMBER_RE = re.compile(r"(?<![A-Za-z])[-+]?\d[\d,]*(?:\.\d+)?%?(?![A-Za-z])")
