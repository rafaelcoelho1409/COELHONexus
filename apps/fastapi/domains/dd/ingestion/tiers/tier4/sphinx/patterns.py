from __future__ import annotations

import re


# RTD subtrees like `/en/stable/` include the version, so a direct
# `/en/stable/objects.inv` probe works. Bare `/en/` (no version) probes
# multiple candidates. Matches: stable, latest, main, master, dev, v?N…
VERSION_RE = re.compile(r"^(?:stable|latest|main|master|dev|v?\d.*)$")


# Sphinx auto-generated pages + asset directories to exclude from discovery.
EXCLUDE_PATH_RE = re.compile(
    r"(?:^|/)(?:search|genindex|genindex-[a-z]|py-modindex|modindex)\.html$"
    r"|/_(?:modules|sources|static|images|downloads)/"
)


# Non-page binary downloads — leave for asset crawlers.
EXCLUDE_EXT_RE = re.compile(
    r"\.(?:ipynb|zip|tar\.gz|tgz|pdf|png|jpe?g|gif|svg|ico|"
    r"woff2?|ttf|otf|eot|mp4|webm|webp|css|js|map)$",
    re.IGNORECASE,
)


# Slug normalization for sub-page URLs.
SLUG_RE = re.compile(r"[^a-z0-9]+")
