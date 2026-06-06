"""ycs/chunker — RecursiveCharacterTextSplitter tunables.

Mirror of deprecated `services/youtube/chunker.py:L31-56` defaults."""
from __future__ import annotations


# 2000 chars ≈ 500 NIM tokens (rule of thumb for English).
CHUNK_SIZE_CHARS = 2000
CHUNK_OVERLAP_CHARS = 200

# Recursive separator priority — paragraph → line → sentence → word →
# raw chars. Higher-priority separator wins when chunks still fit
# under CHUNK_SIZE_CHARS.
SEPARATORS: tuple[str, ...] = ("\n\n", "\n", ". ", " ", "")
