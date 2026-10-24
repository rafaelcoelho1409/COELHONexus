"""Pre-compiled regexes for the RR agent's fs tools."""
from __future__ import annotations

import re


# Valid JSON escape chars per RFC 8259. Anything else after a single backslash is malformed.
STRAY_BS_RE: re.Pattern[str] = re.compile(r'\\(?!["\\/bfnrtu])')

# A `\u` not followed by exactly 4 hex digits is malformed — double the backslash.
TRUNCATED_U_RE: re.Pattern[str] = re.compile(r'\\u(?![0-9a-fA-F]{4})')

MISSING_COMMA_BRACE_RE:   re.Pattern[str] = re.compile(r'(})(\s*)(\{)')
MISSING_COMMA_BRACKET_RE: re.Pattern[str] = re.compile(r'(])(\s*)(\[)')
MISSING_COMMA_QUOTE_RE:   re.Pattern[str] = re.compile(r'(")(\s*\n\s*)(")')
