from __future__ import annotations

import re


# A run of anything a slug does not keep.
NON_SLUG_RE = re.compile(r"[^a-z0-9]+")

# A `<script>` or `<style>` block with its content, and any tag: what is left after taking them off is the text a reader sees.
SCRIPT_BLOCK_RE = re.compile(r"<script[^>]*>.*?</script>", re.DOTALL | re.IGNORECASE)
STYLE_BLOCK_RE = re.compile(r"<style[^>]*>.*?</style>", re.DOTALL | re.IGNORECASE)
TAG_RE = re.compile(r"<[^>]+>")

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
HYDRATED_SPA_RE = re.compile(
    r'<script[^>]+id\s*=\s*["\']?__NEXT_DATA__'
    r'|window\.__NUXT__\s*='
    r'|window\.___gatsby\s*='
    r'|__remixContext\s*[:=]'
    r'|window\.__INITIAL_STATE__\s*='
    r'|window\.__APOLLO_STATE__\s*=',
    re.IGNORECASE,
)
