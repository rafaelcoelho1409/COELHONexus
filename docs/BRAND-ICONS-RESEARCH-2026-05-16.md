# Brand Icons — Source Catalog (2026-05-16)

Research output for mapping the 115 frameworks in `apps/fastapi/files/sources.yaml`
to logos for use as buttons/badges in the FastHTML UI.

No single source covers 100% of the catalog (mainstream DevOps/ML tools are
well-covered; niche academic libraries like ADTK, Skforecast, SHAP-IQ,
TerraTorch, RecBole, Yellowbrick are not). A three-tier fallback gets full
coverage.

## Tier 1 — Simple Icons (default)

- URL pattern: `https://cdn.simpleicons.org/<slug>`
- ~3,400 brand SVGs, CC0 license, zero auth, smallest payload
- Covers ~85% of the catalog
- Single-color SVG by default; tint via path suffix: `https://cdn.simpleicons.org/docker/2496ED`
- Site: <https://simpleicons.org>

## Tier 2 — devicon (gap-filler)

- URL pattern: `https://cdn.jsdelivr.net/gh/devicons/devicon/icons/<slug>/<slug>-original.svg`
- Developer-tool focused; multi-color variants (`-original`, `-plain`, `-line`)
- Better coverage for several Python/ML tools Simple Icons misses
- Site: <https://devicon.dev>

## Tier 3 — GitHub org/user avatar (universal fallback)

- URL pattern: `https://github.com/<org_or_user>.png?size=80`
- Works for 100% of the catalog because every entry in `sources.yaml` has a
  `github:` URL — strip the org/user segment to feed this endpoint
- Catches the niche academic libraries no logo library publishes
- Example: `github:https://github.com/arundo/adtk` → `https://github.com/arundo.png?size=80`

## Badges — Shields.io

For the standard "logo + label + color pill" badge pattern:

- URL pattern: `https://img.shields.io/badge/<label>-<color>?logo=<simple_icons_slug>&logoColor=white`
- Example: `https://img.shields.io/badge/Docker-2496ED?logo=docker&logoColor=white`
- The `logo` query param accepts any Simple Icons slug
- Drops directly into HTML as `<img>` — no client-side rendering needed
- Site: <https://shields.io>

## Suggested wiring

Add an optional `logo_slug` field per framework in `sources.yaml`:

```yaml
- name: Docker
  docs: https://docs.docker.com/
  github: https://github.com/docker/cli
  logo_slug: docker          # Simple Icons slug
  logo_color: "2496ED"       # optional brand hex for badges / tinted SVGs
```

FastHTML render logic (~5 lines): if `logo_slug` is set, render
`cdn.simpleicons.org/<slug>`; otherwise derive org from `github:` and render
`github.com/<org>.png?size=80`. Two endpoints, no extra deps.

## Alternatives considered

- **theSVG** (~5,880 icons, MIT, has an MCP server) — broader than Simple
  Icons but newer/less battle-tested. Worth revisiting if Simple Icons +
  devicon gaps prove painful.
- **Tech Stack Icons** (~700 icons, dark/light/grayscale) — niche, may help
  for the ML/data subset.
- **Super Tiny Icons** (~475 icons, all <1 KB) — only useful if rendering
  in size-critical contexts (e.g., email templates).

## Sources

- <https://simpleicons.org>
- <https://devicon.dev>
- <https://shields.io>
- <https://dev.to/thegdsks/free-svg-brand-icons-in-2026-thesvgorg-vs-svgl-vs-simple-icons-44od>
- <https://www.tech-stack-icons.com/>
- <https://github.com/glincker/thesvg>
