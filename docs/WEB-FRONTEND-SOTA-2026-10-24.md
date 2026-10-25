# apps/web — assessment, design direction and engineering standards (2026-10-24)

Scope: (1) assess the `apps/web` slice built with opencode; (2) the visual direction for a non-generic, premium
product UI; (3) the engineering standards for the whole app and the strangler migration off `apps/fasthtml`.
Every number below was measured on the repo or fetched from a primary source on 2026-10-24 (evidence list at the end).

---

## 1. Assessment of `apps/web` (as it stands)

**What it is:** one static marketing page (`/`), a `/health` route, 1,196 lines of TS/CSS, 1 test. Next 16.3.8, React
19.2.8, Tailwind 4.3.3, shadcn on Base UI, TanStack Query. Wired into skaffold, the Helm chart (`templates/web`) and the
ArgoCD image updater. **Untracked in git** (`git ls-files apps/web` = 0).

**Verified green:** `npm run lint`, `typecheck`, `test` (1/1) and `build` all pass; strict TS; exact-pinned versions;
multi-stage standalone Dockerfile running as `node` (non-root); skip link, landmarks, `prefers-reduced-motion`; contrast
of every text pair passes WCAG AA (ivory 16.7:1, muted 7.6:1, muted/80 5.2:1, oxblood text 5.5:1, button 6.1:1).

**Findings, ranked**

| # | Sev | Finding | Evidence | Fix |
|---|---|---|---|---|
| 1 | **P0** | `next@16.3.8` carries 2 CRITICAL RCEs + 1 HIGH DoS | OSV: GHSA-2464-fhr4-w9xw (SVG image optimizer, glibc Linux — your `node:24-slim` is glibc), GHSA-673q-mjm6-r5xr (`use cache`), GHSA-p3m5-59mp-jv5r (Server Actions). Fixed in 16.4.1; **16.4.3 (2026-10-23) = 0 advisories** | `next@16.4.3`. Current code uses no `use cache`, Server Actions or `next/image`, so exploitability is probably low — patch anyway |
| 2 | **P1** | Dockerfile `COPY /app/public` fails from a clean clone | `public/` is empty and git does not track empty dirs | add `public/.gitkeep` (or drop the COPY) |
| 3 | **P1** | `npm audit`: 4 vulns (3 high, 1 critical), all `shadcn → @shadcn/registry → zod` | `shadcn` is a **runtime** dependency (it is a CLI) | `shadcn@4.22.1` (fixed range ends 4.21.5) and move to `devDependencies` |
| 4 | **P1** | Homepage content is stale/static | advertises **Research Radar** as a live third product (rr is being replaced); "Operational/Live" and the metric numbers are hard-coded; hero says "Solo-built · 100% free-tier LLM" | wire to real data (§3.2) or remove; move provenance claims to `/about` |
| 5 | **P1** | Readability tic | **46** uses of 8–11px text; 42 tracked uppercase mono labels, 33 `uppercase` | 12px floor; reserve mono caps for metadata only |
| 6 | P2 | TS config is only `strict` | missing `noUncheckedIndexedAccess`, `noImplicitOverride`, `verbatimModuleSyntax`, `noFallthroughCasesInSwitch`; `target ES2017`, `allowJs: true` | §3.3 |
| 7 | P2 | No formatter, minimal ESLint, no boundaries, 1 test, no e2e/a11y/visual tests, no typed API client (`SystemStatus` hand-types `Health` and compares to `"healthy"`) | — | §3.4–3.8 |
| 8 | P2 | Flat `components/` by page section, no feature layer | — | §3.1 |
| 9 | P3 | `cn@0.4.0` instead of clsx + tailwind-merge | works (0 OSV advisories; dedupes `px-2 px-4`) but it is a single-maintainer micro-package | keep, or revert to the standard pair |

**Design verdict:** the identity is deliberate and avoids the usual slop tells (no purple/indigo, no emoji icons, no glass,
no oversized radii, no three-equal-cards-over-dark-image hero; authored neutrals; 2px radius; hairlines). It reads as an
"operator console". The weaknesses are density of tiny caps labels and that it is a **brochure** — a premium dev-tool
sells through the product itself, and the product UI does not exist yet.

---

## 2. Visual direction

**Decision: keep and refine the obsidian / oxblood operator-console language; do not restart.** Add a real design-token
system, a light theme, and build the product surfaces (not the brochure) first.

| Option | What it is | Verdict |
|---|---|---|
| **A. Refined operator console (current direction)** | dark graphite, ivory text, one oxblood accent, hairlines, 2px radii | **Pick.** Distinctive, already built, data-dense friendly |
| B. Geist-style monochrome | near-white/near-black, no accent, aggressive negative tracking | Strong but reads as "Vercel clone" |
| C. Linear-style calm neutral + one accent | LCH-generated themes, Inter Display headings | Great system to *borrow from* (below), not a look to copy |

**What 2026 sources say separates crafted from slop** (noqta, superdesign): models output the statistical average — Tailwind's
indigo default, Inter everywhere, one oversized radius on everything, three equal cards, centered hero over a dark image,
"John Doe/Acme" placeholders. The fix is a **written spec before code** (a `DESIGN.md`: tokens, type scale, density, motion,
do/don't), several directions compared, then code — you cannot prompt taste into an averaging system.

**Token system (borrow from Linear + Radix + Geist):**
- Linear: themes are generated from **3 inputs — base, accent, contrast** in LCH (perceptually uniform), not 98 hand-set
  variables; a contrast input yields high-contrast themes; limit accent chroma in neutrals so greys feel timeless.
- Radix: **12 semantic steps** — 1-2 app/subtle bg, 3-5 component bg (normal/hover/active), 6-8 borders (subtle/interactive/focus),
  9-10 solid, 11-12 low/high-contrast text. Geist: 10 steps with the same roles; solid grey borders, alpha only to blend.
- Do: define neutral + oxblood accent + 4 status scales (success/warn/danger/running) with those 12 roles in **OKLCH**
  (Tailwind v4's native space), generated by a small script from 3 inputs; ship **light and dark** (the Study reader is long-form
  reading + code — dark-only is the wrong default there); expose via `@theme`.

**Type & density rules**
- Keep Archivo + JetBrains Mono (distinct, not Inter-default). Sans for UI/body, mono only for ids, code, numbers, metadata.
- Scale 12 / 13 / 14 / 16 / 20 / 28 / 40 / 56; **12px floor**; `tabular-nums` on every numeric column; tracking only on display sizes.
- Two densities: comfortable (40px rows) and compact (32px rows) for tables/run lists. Radius set {0, 2, 4, 8} used by role.
- Elevation = border + one subtle shadow; no glows, no gradients as decoration.
- Motion 120–200ms ease-out; React 19.3's stable `<ViewTransition>` for route/list changes; always honor reduced-motion.

**Money-attractive = product proof, not adjectives** (Evil Martians, 100 dev-tool pages: "no salesy BS", problem-oriented stories beat
function lists; Linear's page is the product running in the browser; one clear promise in 3 seconds):
1. Replace the static `ControlPlane` mock with a **replay of a real run** from your API (real planner/synth progress).
2. Hero = one promise ("Turn any framework's docs into a study guide. Ask any channel."), one primary CTA, product visual immediately.
3. Build first the surfaces people pay attention to: live pipeline run view, usage/cost drawer, Study reader, Ask with citations,
   command palette (⌘K). Real data in every screenshot — no "Acme".
4. Static, fast marketing routes (LCP ≤ 2.5 s); drop "Solo-built / free-tier" from the hero.

**Data-dense patterns:** TanStack Table 9 + `@tanstack/react-virtual`; sticky headers, keyboard navigation, inline sparklines;
charts via visx (low-level, so they do not look like default Recharts — judgment call); empty/loading skeletons that match layout.

---

## 3. Engineering standards (the `apps/server`-grade bar for `apps/web`)

### 3.1 Architecture — feature slices mirroring the backend

Take bulletproof-react's `features/` with FSD's **public-API rule**; do not adopt full FSD (it forces renaming `app`/`pages` to
`_app`/`_pages` in Next and adds six layers we do not need).

```
apps/web/
  app/                      # Next routing ONLY: page.tsx/layout.tsx re-export from features (thin)
  src/
    features/
      dd/                   # one folder per backend bounded context: dd, ycs, settings, (new feature)
        api/                #   generated-client wrappers + query keys (keys.ts) + query options  (≈ service.py/keys.py)
        domain.ts           #   pure functions: formatters, reducers, selectors (no I/O)           (≈ domain.py)
        entities.ts         #   UI-level types/zod schemas                                         (≈ entities.py)
        params.ts           #   constants, tunables                                                (≈ params.py)
        components/ hooks/  #   UI + hooks
        index.ts            #   the ONLY import surface other code may use
    shared/                 # ui kit (shadcn/Base UI), lib, config (zod env), api core, sse, styles/tokens
```
Rules (enforced, §3.4): `app → features → shared`; **a feature never imports another feature** except through its `index.ts`;
`shared` imports nothing above it; no deep imports into a feature. Barrels only at the feature root (nested barrels slow compiles).

### 3.2 Data flow — one rule per concern

- **Server Components fetch from the source** (FastAPI at the internal `SERVER_URL`) with the generated SDK — Next's own guide says
  fetch in Server Components directly, not through Route Handlers. **TanStack Query** for anything interactive, polled or streamed.
- **No Server Actions.** Mutations go straight to FastAPI. (Next docs: actions are queued and meant for mutations; and the 14 Oct
  advisory's DoS only hits apps that have at least one Server Action — zero actions = zero exposure.)
- Browser → API stays same-origin through `rewrites` (exists today, 30-minute proxy timeout). Cookies/auth, if added later,
  terminate in a `proxy.ts`, never in client code.
- **Cache Components:** Next ≥16.4 recommends them for every new app (default in 17). Enable `cacheComponents` +
  `partialPrefetching` on 16.4.3 for marketing/static shells; keep product pages dynamic behind Suspense. (The `use cache` RCE is
  fixed in 16.4.1.)

### 3.3 TypeScript & config
- `typescript@6.0.3` today. **TS 7.0.2 (2026-07-08, native Go compiler, ~9–12× faster) ships without a stable programmatic API
  until 7.1** (7.1.0-beta exists), so typescript-eslint/framework tooling cannot use it yet; `@typescript/typescript6` is the bridge.
  Plan: TS 6 now, move to 7.1 when Next/knip/eslint support it.
- `tsconfig`: `strict`, `noUncheckedIndexedAccess`, `noImplicitOverride`, `noFallthroughCasesInSwitch`, `verbatimModuleSyntax`,
  `exactOptionalPropertyTypes`, `target ES2022`, `allowJs: false`, `typedRoutes: true` in `next.config.ts` (verify on 16.4).
- Env validated at startup with zod (`shared/config/env.ts`); `SERVER_URL` documented as build-time because rewrites are compiled.

### 3.4 Lint, format, boundaries, dead code
| Approach | Verdict |
|---|---|
| **Biome 2.6.3 (format + general lint) + ESLint 10 reduced to `eslint-config-next` + `eslint-plugin-boundaries` 7.3** | **Pick.** Biome has React/Next domains, hooks rules, a11y rules, but **no layering rule** and React-Compiler lint is nursery → keep ESLint for exactly those two jobs |
| ESLint + Prettier | Works, slower, two formatters' worth of config |
| Biome only | Simplest, but no import-layer enforcement |
`knip` 6.43 in CI (unused files/exports/deps). Zero warnings policy; `biome ci` + `eslint` + `tsc` + `knip` + tests in one `npm run verify`.

### 3.5 Typed API client from FastAPI
| Option | Maturity / version | Trade-off | Pick when |
|---|---|---|---|
| **`@hey-api/openapi-ts` 0.99.0 (2026-06-22)** | **named in FastAPI's official "Generate Clients" doc**; fetch client, TanStack Query plugin (`queryOptions`/`mutationOptions`/infinite), **SSE via async generators since 0.87.2** | **0.x with breaking changes every 3–5 minors** (0.96 needs Node ≥22.13; 0.91 ESM-only) → pin exact, regenerate in CI | **Default** |
| Orval 8.44.0 | stable v8; hooks + zod + **MSW mocks** from one config; axios + react-query runtime | no first-class SSE | if you want generated mocks |
| openapi-typescript 7.13.0 + openapi-fetch | types only, 0 runtime | you hand-write hooks | maximum control |
Workflow: `generate_unique_id_function` on FastAPI (`tag-name` ids), `npm run api:generate` from `openapi.json`, **commit the generated
client** and fail CI on drift (`git diff --exit-code`). **Backend follow-up:** FastAPI ≥0.135 (you run 0.136.3) has native
`EventSourceResponse` + `AsyncIterable[Event]` that puts the **stream item type in OpenAPI** — migrating the planner/synth/rr/ask
streams gives typed events end to end.

### 3.6 Streaming (SSE) — measured, not assumed
Old UI: `EventSource` (GET) for planner/synth/rr, `fetch` + `getReader()` for POST (Ask, query AI).
New: one `shared/sse` module on `fetch` streaming + `eventsource-parser` 4.1.2 (supports POST; `@microsoft/fetch-event-source` is
unmaintained since 2021) with reconnect/backoff, `Last-Event-ID`, abort on unmount, and a zod-validated event union per stream.

**Proxy test I ran** (scratch Next 16.3.8 standalone → fake SSE backend, events 1 s apart):
| Path | Result |
|---|---|
| direct to backend | streamed live (0,1,2,3,4,5 s) |
| through Next rewrite, backend sends `Cache-Control: no-cache, no-transform` (your current streams do) | streamed live |
| through Next rewrite, **plain `text/event-stream`** (what FastAPI's `EventSourceResponse` documents: only `no-cache`) | **gzip'd and BUFFERED — all events arrived together at 6 s** |
| same, with **`compress: false`** in `next.config.ts` | streamed live |
→ **Set `compress: false`** (let the ingress compress) before touching the backend SSE code.

### 3.7 Testing, a11y, performance, CI
- **Vitest 5** + Testing Library (units, hooks, components); **MSW 3** at the network boundary; **Playwright 1.64 + `@axe-core/playwright`**
  for e2e flows and WCAG 2.2 AA scans (4.5:1 text, 3:1 non-text, 24px targets); visual-regression snapshots for the design system.
- Per-feature test colocated; contract test = the generated-client drift check.
- Budgets (our targets; Core Web Vitals "good"): LCP ≤ 2.5 s, INP ≤ 200 ms, CLS ≤ 0.1; marketing route JS budget enforced with the
  Turbopack Bundle Analyzer (16.4 snapshots/diffs it).
- CI (one job graph): install → `biome ci` → `eslint` → `tsc` → `knip` → vitest → `api:generate` drift → build → Playwright+axe →
  `npm audit --omit=dev` + OSV gate → image build. Renovate/uv-style **7-day cooldown** on new releases (same policy as `apps/server`).

### 3.8 Migration — strangler fig, by Next's own mechanism
Next's `rewrites().fallback` runs **after all filesystem and dynamic routes** and the docs call it the pattern for incremental
adoption. Put `apps/web` in front, fall back to FastHTML for anything not yet migrated:
```ts
async rewrites() { return { afterFiles: [/* /api, /docs, /openapi.json → SERVER_URL */],
                            fallback: [{ source: "/:path*", destination: `${FASTHTML_URL}/:path*` }] }; }
```
Migrating a route = add its folder; **rollback = delete the folder**. Ingress points at `web`; `fasthtml` becomes internal.

Surface to migrate (measured): FastHTML = 7.3k Py, **24.5k JS**, 12.4k CSS; 14 page routes; biggest: `ycs/ask.js` 2,471,
`rr/main.js` 1,818, `ycs/pipeline_panel.js` 1,142, `dd/synth/lifecycle.js` 1,020. **Do not migrate `rr/*`** (~3.5k JS; being replaced).
| Phase | Scope | Exit criteria |
|---|---|---|
| 0 | harden: `next@16.4.3`, `public/.gitkeep`, `shadcn` fix, `git add apps/web`, CI + Biome/boundaries/knip, tsconfig, DESIGN.md | `verify` green; image builds from a clean clone |
| 1 | design tokens (light/dark), app shell, ⌘K, generated client, `shared/sse`, `compress:false`, fallback rewrite | health + one real read view live behind `web` |
| 2 | **DD**: ingestion → pipeline → planner → synth → study (core product; SSE EventSource streams) | feature parity + Playwright flows; FastHTML DD routes removed |
| 3 | **YCS**: ingest → source → query → ask (POST SSE) | same |
| 4 | settings (BYOK), new feature that replaces rr | same |
| 5 | cut-over: ingress → web, delete `apps/fasthtml` + `proxy.py` | no fallback traffic for 7 days |

---

## Caveats & unknowns
- **hey-api** is the evidence-backed default but pre-1.0; its SSE API and the zod plugin were confirmed only from the changelog
  (not run); do a 1-day spike on the planner stream before committing. Orval is the fallback.
- **Linear's redesign article is undated** and the Evil Martians study is from 2025 (older than the 12-month window); used for
  principles, not versions.
- "Dark-only is wrong for the Study reader" and "visx over Recharts" are **design judgment**, not sourced facts.
- Exploitability of the three Next advisories against your current code was not tested; patching is still the answer.
- The SSE buffering test used Next **16.3.8** standalone; re-run it after upgrading to 16.4.3 (cheap: the scratch probe exists).
- Performance/a11y budgets are proposed targets, not measured on a built product UI (there is none yet).
- Package versions are as of 2026-10-24 from the npm registry; the TypeScript 7.1 tooling story is moving fast.

## Evidence
- Next.js release/blog index (16.4 on 2026-10-06; security updates 2026-09-30, 2026-10-14) — https://nextjs.org/blog
- Next.js 16.4 (Cache Components recommended for all apps; `ensureStatic`; React 19.3) — https://nextjs.org/blog/next-16-4 (2026-10-06)
- Oct 14 2026 security update (2 Critical RCE + 1 High DoS; fixed in 16.4.1) — https://nextjs.org/blog/nextjs-security-update-october-14-2026
- OSV: next@16.3.8 → GHSA-2464-fhr4-w9xw, GHSA-673q-mjm6-r5xr, GHSA-p3m5-59mp-jv5r; next@16.4.3 → none — https://api.osv.dev (queried 2026-10-24)
- Next.js "Backend for Frontend" guide (fetch in Server Components, Server Actions are queued) — https://nextjs.org/docs/app/guides/backend-for-frontend (updated 2026-06-25)
- Next.js `rewrites` (`fallback`, "Incremental adoption") — https://nextjs.org/docs/app/api-reference/config/next-config-js/rewrites (updated 2026-09-28)
- React 19.3 (stable ViewTransition, Fragment refs, `browser()`) — https://react.dev/blog/2026/09/09/react-19-3 (2026-09-09)
- FastAPI native SSE (`EventSourceResponse`, since 0.135.0, typed `AsyncIterable`, keep-alive ping, `Cache-Control: no-cache`, `X-Accel-Buffering: no`) — https://fastapi.tiangolo.com/tutorial/server-sent-events/
- FastAPI Generate Clients (recommends Hey API) — https://fastapi.tiangolo.com/advanced/generate-clients/
- hey-api changelog (SSE since 0.87.2; 0.99.0 breaking changes; Node ≥22.13) — https://github.com/hey-api/openapi-ts/blob/main/packages/openapi-ts/CHANGELOG.md
- hey-api TanStack Query plugin — https://heyapi.dev/openapi-ts/plugins/tanstack-query
- Orval vs openapi-typescript vs Kubb (secondary) — https://www.pkgpulse.com/guides/orval-vs-openapi-typescript-vs-kubb-openapi-client-2026
- Biome linter domains (React/Next rules; `noPrivateImports`; no layering rule; `useReactCompiler` nursery) — https://biomejs.dev/linter/domains/
- Feature-Sliced Design with Next.js (`_app`/`_pages` rename) — https://feature-sliced.design/docs/guides/tech/with-nextjs
- bulletproof-react project structure (features, unidirectional flow, lint-enforced) — https://github.com/alan2207/bulletproof-react/blob/master/docs/project-structure.md
- TypeScript 7.0 (2026-07-08; no stable programmatic API until 7.1; `@typescript/typescript6`) — https://infoq.com/news/2026/08/typescript-7-released/
- Linear redesign (3-variable LCH themes, Inter Display headings, reduced chrome; undated) — https://linear.app/now/how-we-redesigned-the-linear-ui
- Vercel Geist colors (10 scales × 10 steps, step roles) — https://vercel.com/geist/colors
- Radix Colors 12-step scale — https://www.radix-ui.com/colors/docs/palette-composition/understanding-the-scale
- AI-slop analysis (statistical-average defaults, DESIGN.md spec chain) — https://noqta.tn/en/blog/ai-design-slop-overused-ui-patterns-fix-2026 ; https://www.superdesign.dev/blog/why-ai-design-looks-generic
- Evil Martians, 100 dev-tool landing pages (2025) — https://evilmartians.com/chronicles/we-studied-100-devtool-landing-pages-here-is-what-actually-works-in-2025
- npm registry latest versions/dates (2026-10-24): next 16.4.3 (10-23), react 19.3.1 (10-14), tailwindcss 4.3.3, @hey-api/openapi-ts 0.99.0, @tanstack/react-query 5.104.5, @biomejs/biome 2.6.3, knip 6.43.0, eslint-plugin-boundaries 7.3.0, vitest 5.0.5, @playwright/test 1.64.1, msw 3.0.3, zod 4.6.7, typescript 7.0.2 (6.0.3 latest 6.x), eventsource-parser 4.1.2
- Own measurements on this repo: `npm run lint/typecheck/test/build`, `npm audit`, WCAG contrast script, font-size grep, scratch SSE proxy probe (2026-10-24)
