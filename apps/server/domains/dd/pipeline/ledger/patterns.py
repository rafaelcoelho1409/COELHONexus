import re


# A shell line that installs a package: setup, not an example to teach.
INSTALL_RE = re.compile(r"^\s*(?:\$\s*)?(?:pip3?|uv\s+pip|uv\s+add|poetry\s+add|npm|pnpm|yarn|brew|apt(?:-get)?|go\s+get|cargo\s+add|conda)\s+(?:install|add|i)\b", re.IGNORECASE)
# Words that mark a block as the old way of doing something.
DEPRECATED_RE = re.compile(r"\b(?:deprecated|legacy|obsolete|no longer (?:supported|recommended)|old (?:way|api|style))\b", re.IGNORECASE)
# An interactive session's prompt: the block shows output as well as code.
PROMPT_RE = re.compile(r"^(?:>>>|\.\.\.)\s", re.MULTILINE)
# An identifier in code, for comparing two blocks.
IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
