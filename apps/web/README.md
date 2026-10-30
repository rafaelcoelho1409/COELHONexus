# COELHO Nexus — web

The Next.js front end for COELHO Nexus — the only UI. Next.js 16 (App Router, Turbopack) · React 19 ·
TypeScript 6 strict · Tailwind 4 · shadcn/ui on Base UI · TanStack Query · generated API client.

| Surface | Served by |
| --- | --- |
| Home, Settings | this app |
| Docs Distiller (`/dd/*`) | this app; the old `/docs-distiller*` URLs redirect here (307) |
| YouTube content search (YCS: Source, Ingestion, Ask, Query) | this app |

The retired FastHTML UI no longer exists in this repo, so there is no fallback to it. The `/docs-distiller`
redirect stays so old bookmarks and shared study links keep working; flip it to `permanent: true`
(`src/shared/config/redirects.ts`) once you no longer need to roll it back.

```bash
npm install
npm run dev            # http://localhost:3000
npm run verify         # lint, typecheck, knip, unit tests, boundaries, tokens, API drift, public copy, backend contract
npm run verify:full    # verify + production build + budgets + Playwright/axe e2e
```

Standards, evidence and the migration plan: [`docs/WEB-FRONTEND-SOTA-2026-10-24.md`](../../docs/WEB-FRONTEND-SOTA-2026-10-24.md).
Visual and content rules: [`DESIGN.md`](DESIGN.md).

## Structure

```
src/app/                    routing only — one-line re-exports of feature pages, layout, /health
src/features/<context>/     api · domain · entities · params · components · hooks · index.ts
src/shared/                 api (generated client) · config (env, rewrites, redirects) · lib · markdown · providers · sse · styles · ui
```

Import layers are enforced (`eslint-plugin-boundaries`, proven by `npm run test:boundaries`):
`app → features → shared`; a feature is imported only through its `index.ts`; features never import each
other's internals.

## Talking to the API

- **Typed client**: generated from the committed `openapi/openapi.json` into `src/shared/api/generated`
  (`npm run api:generate`). Import from `@/shared/api` — `getDdRunsActive`, `getDdRunsActiveOptions`, …
  The spec itself comes from the backend: `apps/server/.venv/bin/python scripts/server/export_openapi.py`
  (`--check` for drift).
  `npm run api:check` fails if the generated client was hand-edited or the spec changed without regenerating.
- **Streams**: `@/shared/sse` — `streamEvents` (async generator: reconnect with backoff and `Last-Event-ID`,
  4xx is permanent, abort-safe) and `useEventStream`. Next must not compress event streams: `compress: false`
  is set for that reason (measured: a gzip'd `text/event-stream` arrives in one lump when it ends).
- **Same-origin**: the browser only calls `/api/*`; Next proxies it to the server.

## Configuration (build time)

Routing is compiled into the build, so these are read by `next dev`/`next build` — in Kubernetes they are
Docker build args (`Dockerfile.web`, `skaffold.yaml`). See `.env.example`.

| Variable | Default | Effect |
| --- | --- | --- |
| `SERVER_URL` | `http://localhost:23030` | The Nexus API; `/api/*` is proxied here |
| `EXPOSE_API_DOCS` | `false` | Also proxy `/docs` and `/openapi.json` |

`/health` is this app's own probe and never touches the backend.

## Tooling

| Command | Checks |
| --- | --- |
| `npm run lint` | Biome (format, lint, a11y) + ESLint (Next rules, import boundaries) |
| `npm run typecheck` | `strict`, `noUncheckedIndexedAccess`, `verbatimModuleSyntax`, … |
| `npm run knip` | unused files, exports and dependencies |
| `npm run test` | Vitest + Testing Library + MSW |
| `npm run tokens:check` | colour tokens current; contrast rules hold in 4 themes |
| `npm run copy:check` | public copy mentions no routing layer, provider or retired product |
| `npm run budgets` | first-load JS/CSS/HTML gz budgets (after `build`) |
| `npm run test:e2e` | Playwright against the standalone build: axe WCAG 2.2 AA, keyboard, theme, 12px floor |

`exactOptionalPropertyTypes` is deliberately off: the generated client's runtime does not compile under it.
