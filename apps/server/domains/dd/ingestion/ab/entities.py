from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field


@dataclass
class Inputs:
    """What every arm is handed for one source, and nothing else: so arms differ only in what they do with it."""
    framework_slug: str
    tier:           str                   # the catalog kind the capture came from: `llms_full`
    url:            str                   # where the capture came from
    text:           str                   # the bundle cut to the catalog's `path_filter` — what arms transform, what fidelity is measured against
    nav_text:       str = ""              # the site's own page index (`llms.txt`), "" when there is none
    nav_links:      list = field(default_factory = list)   # [(title, url, section), ...] parsed from it, in its order
    params:         dict = field(default_factory = dict)   # what the catalog said: `path_filter`, `nav_url`, `code_language`
    capture_sha256: str = ""              # of the captured bundle: which bytes this A/B ran on


@dataclass
class Analysis:
    """A text read once as a reader reads it: its code blocks and its prose lines."""
    fences:      list[str]
    prose_lines: list[str]
    tokens:      Counter = field(default_factory = Counter)
