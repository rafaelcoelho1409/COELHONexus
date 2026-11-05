from __future__ import annotations
import re


DOCUMENT_SUFFIX_RE = re.compile(r"\.(?:pdf|epub)$", re.IGNORECASE)
SOURCE_SITE_TAG_RE = re.compile(
    r"\s*[(\[][^()\[\]]*(?:z-?lib(?:rary)?|1lib|libgen|pdfdrive|anna['’]?s[\s-]?archive|b-ok|bookzz)[^()\[\]]*[)\]]",
    re.IGNORECASE,
)
