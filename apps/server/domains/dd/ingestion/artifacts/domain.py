"""Pure artifact-extraction transforms (no I/O). Fetch + write live in service.py."""
from __future__ import annotations
import domains
from . import entities, keys, params, patterns

import base64
import hashlib
from collections.abc import Iterator
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup



def safe_name(name: str | None) -> str:
    """A requested artifact name without surrounding slashes or `..`."""
    return (name or "").strip().strip("/").replace("..", "")


def is_valid_name(name: str) -> bool:
    """Whether a `safe_name` can name an artifact: something, and no folder."""
    return bool(name) and "/" not in name


def media_type_of(name: str) -> str:
    """The media type an artifact is served with, from its file extension."""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return keys.EXT_MIME.get(ext, params.DEFAULT_MEDIA_TYPE)


def hash_name(data: bytes, ext: str) -> str:
    return hashlib.sha256(data).hexdigest()[: params.NAME_HASH_CHARS] + "." + (ext or params.DEFAULT_EXT)


def _url_ext(url: str) -> str | None:
    """The extension at the end of a URL's path (no query or fragment), lower-cased; None when it has none."""
    parts = (urlparse(url).path or "").rsplit(".", 1)
    if len(parts) != 2:
        return None
    return parts[1].split("?", 1)[0].split("#", 1)[0].lower()


def ext_from_url(url: str) -> str:
    ext = _url_ext(url)
    return ext if ext in keys.EXT_MIME else ""


def is_imageish_url(url: str) -> bool:
    return _url_ext(url) in keys.IMAGE_EXTS


def parse_data_url(src: str) -> entities.Artifact | None:
    m = patterns.DATA_URL_RE.match(src)
    if not m:
        return None
    mime = (m.group("mime") or "").lower()
    enc = m.group("enc")
    data_str = m.group("data") or ""
    try:
        if enc == "base64":
            data = base64.b64decode(patterns.WHITESPACE_RE.sub("", data_str), validate = False)
        else:
            data = data_str.encode("utf-8")
    except ValueError:  # a payload that does not decode (binascii.Error, UnicodeEncodeError) is not an artifact
        return None
    if not (params.MIN_ARTIFACT_BYTES <= len(data) <= params.MAX_ARTIFACT_BYTES):
        return None
    ext = keys.MIME_EXT.get(mime, params.DEFAULT_EXT)
    return entities.Artifact(
        name         = hash_name(data, ext),
        data         = data,
        content_type = mime or params.DEFAULT_MEDIA_TYPE,
        source_url   = params.DATA_URL_PREFIX,
    )


def artifact_from_response(url: str, status_code: int, content_type: str | None, data: bytes) -> entities.Artifact | None:
    """The artifact a remote fetch gave, or None when it is not one: not a 200, a size outside the bounds, or an HTML / text / JSON
    body (the 404 page a server answers with status 200)."""
    if status_code != 200:
        return None
    if not (params.MIN_ARTIFACT_BYTES <= len(data) <= params.MAX_ARTIFACT_BYTES):
        return None
    ct = (content_type or "").split(";", 1)[0].strip().lower()
    if ct.startswith(params.REJECTED_CONTENT_TYPES):
        return None
    ext = keys.MIME_EXT.get(ct) or ext_from_url(url) or params.DEFAULT_EXT
    if not ct:
        ct = keys.EXT_MIME.get(ext, params.DEFAULT_MEDIA_TYPE)
    return entities.Artifact(name = hash_name(data, ext), data = data, content_type = ct, source_url = url)


def versioned_base(final_url: str) -> str:
    """The folder a host's root led to (`https://x.dev/` → `https://x.dev/en/stable/`), with its trailing slash — where a versioned
    site (Read the Docs, Sphinx) keeps its pages and its `_images/`."""
    parsed = urlparse(final_url)
    path = parsed.path or "/"
    if not path.endswith("/"):
        path = path.rsplit("/", 1)[0] + "/"
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def image_fallback_urls(url: str, version_base: str | None) -> list[str]:
    """Where a 404'd image may really be: Sphinx's `_images/` folder under the docs' versioned base, then under the host's root. A
    Tier 1 bundle's references are page-relative, so the real asset is at `{versioned base}/_images/{basename}`. Empty when `url`
    is not an image, is already in `_images/`, or has no file name — and `version_base` is only needed when it is a candidate."""
    if not is_imageish_url(url):
        return []
    p = urlparse(url)
    basename = (p.path or "").rsplit("/", 1)[-1]
    if not basename or f"/{params.SPHINX_IMAGES_DIR}" in (p.path or ""):
        return []
    candidates: list[str] = []
    for c in (
        urljoin(version_base or f"{p.scheme}://{p.netloc}/", f"{params.SPHINX_IMAGES_DIR}{basename}"),
        f"{p.scheme}://{p.netloc}/{params.SPHINX_IMAGES_DIR}{basename}",
    ):
        if c != url and c not in candidates:
            candidates.append(c)
    return candidates


def wants_image_fallback(url: str) -> bool:
    """Whether a failed fetch of `url` is worth looking for in `_images/` (it is an image, not already there, with a file name)."""
    return bool(image_fallback_urls(url, None))


def classify_url(url: str, base: str) -> tuple[str, str] | None:
    """Attribute value → `(kind, resolved_payload)`, kind ∈ {'data','remote'}.
    None for unsupported schemes (mailto:, javascript:, tel:, anchors)."""
    u = (url or "").strip()
    if not u or u.startswith(params.NON_FETCHABLE_PREFIXES):
        return None
    if u.startswith(params.DATA_URL_PREFIX):
        return (params.KIND_DATA, u)
    if u.startswith("//"):
        return (params.KIND_REMOTE, "https:" + u)
    if u.startswith(params.HTTP_SCHEMES):
        return (params.KIND_REMOTE, u)
    full = urljoin(base, u)
    if full.startswith(params.HTTP_SCHEMES):
        return (params.KIND_REMOTE, full)
    return None


def _srcset_density(descriptor: str) -> float:
    """A `srcset` candidate's weight from its descriptor: `2x` is 2.0, `800w` is 0.8, anything else (or unreadable) is 1.0."""
    desc = descriptor.strip().lower()
    try:
        if desc.endswith("x"):
            return float(desc[:-1])
        if desc.endswith("w"):
            return float(desc[:-1]) / params.WIDTH_DENSITY_DIVISOR
    except ValueError:
        pass
    return 1.0


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
        density = _srcset_density(parts[1]) if len(parts) > 1 else 1.0
        full = urljoin(base_url, url)
        if full.startswith((*params.HTTP_SCHEMES, params.DATA_URL_PREFIX)):
            candidates.append((density, full))
    if not candidates:
        return None
    candidates.sort(key = lambda x: -x[0])
    return candidates[0][1]


def _reference(raw: str, attr: str | None, base_url: str) -> tuple[str, str] | None:
    """`(kind, payload)` of a reference found in Markdown: a `srcset` is reduced to its largest candidate first."""
    if attr != "srcset":
        return classify_url(raw, base_url)
    picked = pick_largest_srcset(raw, base_url)
    if not picked:
        return None
    return (params.KIND_DATA, picked) if picked.startswith(params.DATA_URL_PREFIX) else classify_url(picked, base_url)


def _md_references(md: str) -> Iterator[tuple[str, str | None]]:
    """Every media reference in a Markdown page as `(value as written, attribute)`: `![alt](url)` first (attribute None), then the
    attributes of raw HTML media tags embedded in the page."""
    for m in patterns.MD_IMG_RE.finditer(md):
        yield m.group("url"), None
    for tagm in patterns.MD_HTML_TAG_RE.finditer(md):
        for am in patterns.HTML_ATTR_RE.finditer(tagm.group("attrs") or ""):
            value = am.group("value").strip()
            if value:
                yield value, am.group("attr").lower()


def collect_md_payloads(md: str, source_url: str) -> dict[str, str]:
    """Scan `md` for image refs → `{payload: kind}`. Two passes: markdown
    `![alt](url)`, then raw HTML media tags embedded in the markdown."""
    payloads: dict[str, str] = {}
    for raw, attr in _md_references(md):
        cls = _reference(raw, attr, source_url)
        if cls is not None:
            payloads.setdefault(cls[1], cls[0])
    return payloads


def not_own_artifacts(payloads: dict[str, str]) -> dict[str, str]:
    """The payloads that are not already this app's own stored copies (`/api/v1/dd/ingestion/…/artifacts/…`), which are never fetched
    again."""
    return {
        p: k for p, k in payloads.items()
        if not any(marker in p for marker in keys.OWN_ARTIFACT_URL_MARKERS) or "/artifacts/" not in p
    }


def build_md_replacement_map(
    md: str,
    source_url: str,
    payload_to_public: dict[str, str | None],
) -> dict[str, str]:
    """`{original_url_text: public_path}` — keyed on the EXACT URL substring
    so callers can str.replace safely (URLs are unique enough)."""
    rep: dict[str, str] = {}
    for raw, attr in _md_references(md):
        cls = _reference(raw, attr, source_url)
        public = payload_to_public.get(cls[1]) if cls else None
        if public:
            rep[raw] = public
    return rep


def apply_replacements(md: str, replacements: dict[str, str]) -> tuple[str, int]:
    """`md` with each key of `replacements` replaced by its value, and how many replacements were made. Longest keys first, so
    `foo.com/img` cannot eat the front of `foo.com/img.png`."""
    count = 0
    for old in sorted(replacements, key = len, reverse = True):
        count += md.count(old)
        md = md.replace(old, replacements[old])
    return md, count


def html_references(soup: BeautifulSoup, source_url: str) -> list[entities.HtmlReference]:
    """Every media reference in an HTML page (`keys.ARTIFACT_ATTRS`, and the `srcset` of each `<source>`, reduced to its
    largest candidate)."""
    found: list[entities.HtmlReference] = []
    for tag_name, attr in keys.ARTIFACT_ATTRS:
        for el in soup.find_all(tag_name):
            val = (el.get(attr) or "").strip()
            if not val:
                continue
            cls = classify_url(val, source_url)
            if cls is not None:
                found.append(entities.HtmlReference(el, attr, cls[0], cls[1]))
    for el in soup.find_all("source"):
        srcset = (el.get("srcset") or "").strip()
        if not srcset:
            continue
        full = pick_largest_srcset(srcset, source_url)
        if full:
            found.append(entities.HtmlReference(
                el, "srcset", params.KIND_DATA if full.startswith(params.DATA_URL_PREFIX) else params.KIND_REMOTE, full,
            ))
    return found


def rewrite_html_references(references: list[entities.HtmlReference], payload_to_public: dict[str, str | None]) -> int:
    """Point each reference at its stored copy, in place; those whose artifact could not be stored keep their original URL. Returns
    how many were rewritten."""
    rewritten = 0
    for ref in references:
        public = payload_to_public.get(ref.payload)
        if not public:
            continue
        if ref.attr == "srcset":
            ref.element["srcset"] = public
            # Also write sibling src so renderers that ignore srcset still pick up our copy.
            if not (ref.element.get("src") or "").strip():
                ref.element["src"] = public
        else:
            ref.element[ref.attr] = public
        rewritten += 1
    return rewritten


def parse_html(html: str) -> BeautifulSoup:
    """The page parsed with the extractor's parser, or the standard library's when that cannot read it."""
    extract_params = domains.dd.ingestion.tiers.extract.params
    try:
        return BeautifulSoup(html, extract_params.PARSER)
    except Exception:  # noqa: BLE001 — fall back to the standard library's parser
        return BeautifulSoup(html, extract_params.FALLBACK_PARSER)
