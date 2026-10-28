from __future__ import annotations


# Cap per asset — protects single-node K3s memory. Real-world docs ship inline
# notebook-output PNGs up to ~4 MB (UMAP basic_usage.html).
MAX_ARTIFACT_BYTES = 25 * 1024 * 1024

# Drops tracking-pixel-sized payloads.
MIN_ARTIFACT_BYTES = 32

TIMEOUT_S = 30.0

# What the artifacts route answers with: the media type of a name whose extension is not known, and how long a browser may keep a
# file. A name is a SHA-256 of the bytes, so the file under it never changes for the life of the slug.
DEFAULT_MEDIA_TYPE = "application/octet-stream"
CACHE_CONTROL = "public, max-age=31536000, immutable"

# The error codes with which the object store says an object is not there.
MISSING_OBJECT_CODES = ("404", "NoSuchKey")

# Shares tier-4's httpx AsyncClient — keeps connection pool + DNS cache warm.
CONCURRENCY = 8

# A reference is either an inline `data:` URL or a remote one; a name is the first characters of the bytes' SHA-256 and an
# extension (`bin` when the type says none).
KIND_DATA = "data"
KIND_REMOTE = "remote"
DATA_URL_PREFIX = "data:"
NAME_HASH_CHARS = 16
DEFAULT_EXT = "bin"

# What is never a media reference, and the schemes that are fetched.
NON_FETCHABLE_PREFIXES = ("#", "javascript:", "mailto:", "tel:")
HTTP_SCHEMES = ("http://", "https://")

# A `srcset` candidate is `url 2x` (density) or `url 800w` (width); a width counts as this many pixels per unit of density.
WIDTH_DENSITY_DIVISOR = 1000.0

# A server that answers a missing image with a page (a 404 served as 200) gives one of these: it is not the artifact.
REJECTED_CONTENT_TYPES = ("text/html", "text/plain", "application/json")

# An image that 404s is looked for under the docs' versioned base in Sphinx's `_images/` folder; the base is found by following the
# host's root once, within this long.
SPHINX_IMAGES_DIR = "_images/"
VERSION_PROBE_TIMEOUT_S = 15.0
