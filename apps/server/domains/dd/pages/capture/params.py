from __future__ import annotations


# Bump when the shape of `capture.json` changes; `domain.capture_from_json` refuses a newer one than it knows.
SCHEMA_VERSION = 1

# What the files of a capture are to a transform.
ROLE_PAGE = "page"
ROLE_INDEX = "index"

# How a file was fetched (`CapturedFile.via`).
VIA_HTTP = "http"
VIA_TWIN = "twin"
VIA_BROWSER = "browser"

# Written as text: a tier captures what its HTTP client decoded.
TEXT_TYPE = "text/plain; charset=utf-8"
GZIP_TYPE = "application/gzip"
BINARY_TYPE = "application/octet-stream"
GZIP_ENCODING = "gzip"
GZIP_SUFFIX = ".gz"

# A page's file is named after its URL, so a name never depends on the order a crawl finished in: `page-<hash of the URL><extension>`.
PAGE_NAME_PREFIX = "page-"
URL_HASH_CHARS = 16
# The extension a response gets, by what the server says it is, then by what its URL ends with.
EXTENSION_BY_TYPE = (
    ("markdown", ".md"), ("html", ".html"), ("xml", ".xml"), ("json", ".json"), ("python", ".py"),
)
EXTENSION_BY_SUFFIX = (".md", ".mdx", ".txt", ".py", ".html", ".htm", ".xml", ".json", ".rst", ".ipynb")
DEFAULT_EXTENSION = ".txt"

# HTML is kept compressed (a crawl's HTML is several times the size of the Markdown made of it); a gzip level that trades little speed for most
# of the saving.
GZIP_LEVEL = 6
COMPRESS_EXTENSIONS = (".html", ".htm", ".xml")
