"""Pure artifact-extraction transforms (no I/O). Fetch + write live in service.py."""
from __future__ import annotations

import base64
import hashlib
import re
from urllib.parse import urljoin, urlparse

from .entities import Artifact
from .keys import ARTIFACT_ATTRS, EXT_MIME, IMAGE_EXTS, MIME_EXT
from .params import MAX_ARTIFACT_BYTES, MIN_ARTIFACT_BYTES
from .patterns import (
    DATA_URL_RE,
    HTML_ATTR_RE,
    MD_HTML_TAG_RE,
    MD_IMG_RE,
)


def hash_name(data: bytes, ext: str) -> str:
    return hashlib.sha256(data).hexdigest()[:16] + "." + (ext or "bin")


def ext_from_url(url: str) -> str:
    path = (urlparse(url).path or "").rsplit(".", 1)
    if len(path) != 2:
        return ""
    ext = path[1].lower().split("?", 1)[0].split("#", 1)[0]
    return ext if ext in EXT_MIME else ""


def is_imageish_url(url: str) -> bool:
    parts = (urlparse(url).path or "").rsplit(".", 1)
    if len(parts) != 2:
        return False
    return parts[1].split("?", 1)[0].split("#", 1)[0].lower() in IMAGE_EXTS


def parse_data_url(src: str) -> Artifact | None:
    m = DATA_URL_RE.match(src)
    if not m:
        return None
    mime = (m.group("mime") or "").lower()
    enc = m.group("enc")
    data_str = m.group("data") or ""
    try:
        if enc == "base64":
            data = base64.b64decode(re.sub(r"\s+", "", data_str), validate = False)
        else:
            data = data_str.encode("utf-8")
    except Exception:
        return None
    if not (MIN_ARTIFACT_BYTES <= len(data) <= MAX_ARTIFACT_BYTES):
        return None
    ext = MIME_EXT.get(mime, "bin")
    return Artifact(
        name         = hash_name(data, ext),
        data         = data,
        content_type = mime or "application/octet-stream",
        source_url   = "data:",
    )


def classify_url(url: str, base: str) -> tuple[str, str] | None:
    """Attribute value → `(kind, resolved_payload)`, kind ∈ {'data','remote'}.
    None for unsupported schemes (mailto:, javascript:, tel:, anchors)."""
    u = (url or "").strip()
    if not u or u.startswith(("#", "javascript:", "mailto:", "tel:")):
        return None
    if u.startswith("data:"):
        return ("data", u)
    if u.startswith("//"):
        return ("remote", "https:" + u)
    if u.startswith(("http://", "https://")):
        return ("remote", u)
    full = urljoin(base, u)
    if full.startswith(("http://", "https://")):
        return ("remote", full)
    return None


def pick_largest_srcset(srcset: str, base_url: str) -> str | None:
    """Highest-density candidate from a srcset attribute, or None if malformed."""
    candidates: list[tuple[float, str]] = []
    for piece in srcset.split(","):
        piece = piece.strip()
        if not piece:
            continue
        parts = piece.split(None, 1)
        url = parts[0].strip()
        if not url:
            continue
        density = 1.0
        if len(parts) > 1:
            desc = parts[1].strip().lower()
            if desc.endswith("x"):
                try:
                    density = float(desc[:-1])
                except Exception:
                    density = 1.0
            elif desc.endswith("w"):
                try:
                    density = float(desc[:-1]) / 1000.0
                except Exception:
                    density = 1.0
        full = urljoin(base_url, url)
        if full.startswith(("http://", "https://", "data:")):
            candidates.append((density, full))
    if not candidates:
        return None
    candidates.sort(key = lambda x: -x[0])
    return candidates[0][1]


def collect_md_payloads(md: str, source_url: str) -> dict[str, str]:
    """Scan `md` for image refs → `{payload: kind}`. Two passes: markdown
    `![alt](url)`, then raw HTML media tags embedded in the markdown."""
    payloads: dict[str, str] = {}
    for m in MD_IMG_RE.finditer(md):
        cls = classify_url(m.group("url"), source_url)
        if cls is not None:
            payloads.setdefault(cls[1], cls[0])
    for tagm in MD_HTML_TAG_RE.finditer(md):
        attrs = tagm.group("attrs") or ""
        for am in HTML_ATTR_RE.finditer(attrs):
            attr = am.group("attr").lower()
            value = am.group("value").strip()
            if not value:
                continue
            if attr == "srcset":
                picked = pick_largest_srcset(value, source_url)
                if not picked:
                    continue
                cls = (("data", picked) if picked.startswith("data:")
                       else classify_url(picked, source_url))
            else:
                cls = classify_url(value, source_url)
            if cls is not None:
                payloads.setdefault(cls[1], cls[0])
    return payloads


def build_md_replacement_map(
    md: str,
    source_url: str,
    payload_to_public: dict[str, str | None],
) -> dict[str, str]:
    """`{original_url_text: public_path}` — keyed on the EXACT URL substring
    so callers can str.replace safely (URLs are unique enough)."""
    rep: dict[str, str] = {}
    for m in MD_IMG_RE.finditer(md):
        raw = m.group("url")
        cls = classify_url(raw, source_url)
        if cls is None:
            continue
        public = payload_to_public.get(cls[1])
        if public:
            rep[raw] = public
    for tagm in MD_HTML_TAG_RE.finditer(md):
        attrs = tagm.group("attrs") or ""
        for am in HTML_ATTR_RE.finditer(attrs):
            attr = am.group("attr").lower()
            raw = am.group("value").strip()
            if not raw:
                continue
            if attr == "srcset":
                picked = pick_largest_srcset(raw, source_url)
                cls = (("data", picked) if (picked and picked.startswith("data:"))
                       else (classify_url(picked, source_url) if picked else None))
                public = payload_to_public.get(cls[1]) if cls else None
                if public:
                    rep[raw] = public
            else:
                cls = classify_url(raw, source_url)
                public = payload_to_public.get(cls[1]) if cls else None
                if public:
                    rep[raw] = public
    return rep


__all__ = [
    "ARTIFACT_ATTRS",
    "build_md_replacement_map",
    "classify_url",
    "collect_md_payloads",
    "ext_from_url",
    "hash_name",
    "is_imageish_url",
    "parse_data_url",
    "pick_largest_srcset",
]
