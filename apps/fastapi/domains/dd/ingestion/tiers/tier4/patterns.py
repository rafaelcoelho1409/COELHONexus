from __future__ import annotations

import re


# `<div id="root|app|__next|__nuxt|svelte|main-app|gatsby"></div>` — empty
# SPA mount point signals an unhydrated shell.
SPA_ROOT_RE = re.compile(
    r'<div\s+(?:[^>]+\s+)?id\s*=\s*["\']?'
    r'(?:root|app|__next|__nuxt|svelte|main-app|gatsby)'
    r'["\']?\s*[^>]*>\s*</div>',
    re.IGNORECASE,
)

# Hydration-marker scripts emitted by Next.js / Nuxt / Gatsby / Remix / Apollo /
# generic-INITIAL_STATE bundles. Presence ⇒ the body is an SPA shell that won't
# render content without JS.
HYDRATED_SPA_RE = re.compile(
    r'<script[^>]+id\s*=\s*["\']?__NEXT_DATA__'
    r'|window\.__NUXT__\s*='
    r'|window\.___gatsby\s*='
    r'|__remixContext\s*[:=]'
    r'|window\.__INITIAL_STATE__\s*='
    r'|window\.__APOLLO_STATE__\s*=',
    re.IGNORECASE,
)
