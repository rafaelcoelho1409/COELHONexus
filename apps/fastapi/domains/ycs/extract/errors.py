"""ycs/extract — exceptions raised by the input-normalization layer.

yt-dlp subprocess errors live in `domains.ycs.content.errors` (both
modules share the same subprocess wrapper shape) — only the
input-normalization domain errors are local."""
from __future__ import annotations

from domains.ycs.content.errors import YtDlpError


class ExtractError(YtDlpError):
    """Base — anything that surfaces from a metadata-extraction call."""


class InvalidVideoIdError(ExtractError):
    """Couldn't normalize the input into a valid 11-char video id."""


class InvalidChannelIdError(ExtractError):
    """Couldn't normalize the input into a UC… id or @handle."""


class InvalidPlaylistIdError(ExtractError):
    """Couldn't normalize the input into a PL/UU/RD… id."""
