from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CapturedFile:
    """One thing a tier fetched, kept exactly as it came (decoded as the HTTP client decoded it)."""
    name:         str            # its name inside the capture: `bundle.md`, `llms.txt`, `page-<hash>.html`
    role:         str            # what the transform treats it as: `page` (content) or `index` (a list of pages: llms.txt, a sitemap)
    url:          str            # what was asked for
    final_url:    str            # where it was answered from, after redirects
    status:       int            # the HTTP status; 0 when the request itself failed (see `error`)
    content_type: str
    sha256:       str            # of the text as stored: the same capture is the same hash
    bytes:        int
    # A file that was asked for but not kept (a 404, a timeout) is listed all the same, with `stored` off: a transform must know a page
    # was tried and what came of it.
    stored:       bool = True
    encoding:     str = ""       # "gzip" when the object is compressed (HTML is kept compressed; `bytes` is the size before)
    error:        str = ""       # why the request failed, when it did
    for_url:      str = ""       # the page this response stands for, when it is not `url` itself (a page's markdown twin)
    via:          str = ""       # how it was fetched when that matters to a transform: `http`, `twin`, `browser`


@dataclass
class Capture:
    """What one run of one tier fetched, and nothing it did with it: the input of every transform (arm A, arm B, a future
    normalizer). Lives under `ingestion/raw/{framework_slug}/`: `capture.json` (this) beside `files/{name}`."""
    framework_slug: str
    tier:           str          # the catalog kind that was run: `llms_full`
    source_url:     str
    captured_at:    float
    files:          list[CapturedFile] = field(default_factory = list)
    # What the catalog said when the capture was taken and a transform needs (`path_filter`, `nav_url`, `code_language`), and what the
    # tier decided from the network and a transform cannot redo (Tier 4's page list and anchors): the transform depends on these and on
    # the files, on nothing else.
    params:         dict = field(default_factory = dict)
    version:        int = 1
