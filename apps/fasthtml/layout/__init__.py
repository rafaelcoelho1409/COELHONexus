"""Page chrome — global HEAD + top-of-page shell wrapper.

Imported by `main.py` (HEAD) and by every feature module (`_Shell`)."""
from .head import HEAD
from .shell import _Shell


__all__ = ["HEAD", "_Shell"]
