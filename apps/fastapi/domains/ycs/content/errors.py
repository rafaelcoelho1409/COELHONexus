"""ycs/content — exceptions raised by the yt-dlp subprocess wrapper."""
from __future__ import annotations


class YtDlpError(Exception):
    """Base — anything that surfaces from a yt-dlp invocation."""


class YtDlpTimeoutError(YtDlpError):
    """The subprocess didn't finish before the wall-clock budget elapsed."""


class YtDlpSubprocessError(YtDlpError):
    """Non-zero returncode. `stderr` carries yt-dlp's own diagnostic."""

    def __init__(self, stderr: str, returncode: int) -> None:
        super().__init__(stderr)
        self.stderr = stderr
        self.returncode = returncode


class YtDlpJsonParseError(YtDlpError):
    """Subprocess succeeded but stdout wasn't valid JSON."""
