"""synth router — tunables + lookup tables."""
from __future__ import annotations


VALID_ARTIFACTS = {
    "README.md": "text/markdown; charset=utf-8",
}


VALID_MODES = {"quality", "fast"}


SYNTH_LOCK_TTL_S = 21900
