"""ycs/content — string-filter operator prefixes.

yt-dlp's `--match-filter` recognizes these field-comparison operators on
string fields (title, description, channel). Anything else is treated as
a literal string and the caller must wrap it as `field*='value'`."""
from __future__ import annotations


# Order matters — when sniffing a value for "starts with an operator",
# the negated forms (3 chars) must be tried before the plain forms (2
# chars) so `!*=foo` is recognized as `!*=` + `foo`, not `!` + `*=foo`.
STRING_FILTER_OP_PREFIXES: tuple[str, ...] = (
    "!*=", "!^=", "!$=", "!~=",
    "*=", "^=", "$=", "~=",
    "=",
)
