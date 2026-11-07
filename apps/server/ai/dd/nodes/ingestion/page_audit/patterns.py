"""page_audit — pre-compiled regex of the page scans."""
from __future__ import annotations

import re


FENCE_RE = re.compile(r"(`{3,}|~{3,})")
HEADING_WITHOUT_SPACE_RE = re.compile(r"#{1,6}[A-Za-z]")
EMPTY_HEADING_RE = re.compile(r"#{1,6}\s*$")
# UTF-8 text read as Latin-1/Windows-1252 (`Ã©`, `Â `, `â€™`), and the replacement character.
MOJIBAKE_RE = re.compile("[ÃÂ][\u0080-¿]|â€.|�")
