# apps/web — rules for coding agents

You are working in `apps/web`, the Next.js front end of COELHO Nexus. A reviewer (a stronger model) checks
everything you produce, so **precision beats speed**: follow this file and the task brief literally.
If the brief and the code disagree, or you are unsure, **stop and report** — do not improvise.

## Hard rules (never break these)

1. **Never run `git commit`, `git push`, `git add`, `git stash` or `git checkout`.** The human commits.
2. **Only edit files inside `apps/web/`**, and only the paths the brief lists as allowed. Never touch
   `apps/server`, `apps/fasthtml`, `k8s`, `skaffold.yaml`, `infrastructure`, or `docs/` (except the brief's
   own report file if it says so).
3. **Never edit generated or managed files**: `src/shared/api/generated/**`, `src/shared/styles/tokens.generated.css`,
   `openapi/openapi.json`, `package-lock.json` (by hand), `biome.json`, `eslint.config.mjs`, `tsconfig.json`,
   `knip.json`, `next.config.ts`, `playwright.config.ts`. If one of them seems to need a change, stop and report.
4. **No new dependencies** unless the brief names them. If one is named, install it with an exact version
   (`npm install --save-exact name@x.y.z`), never `^`.
5. **Never write the words** rotator, router (model routing), bandit, provider names, BYOK, free-tier,
   Research Radar in any UI text, test fixture or comment. `npm run copy:check` enforces it.
6. **Do not read** outside `apps/web/` unless the brief lists the file. Do not open `.env*` files.
7. **Two failed attempts at the same error → stop** and report the exact error text and what you tried.
   Do not loop, do not weaken a check, do not add `@ts-ignore`, `eslint-disable`, `as any` or `biome-ignore`
   (a `biome-ignore` is allowed only when the brief tells you to add it, with its reason).

## Stack (already installed — do not change)

Next.js 16 (App Router) · React 19 · TypeScript 6 strict · Tailwind 4 · shadcn/ui on **Base UI** (not Radix) ·
TanStack Query 5 · zod 4 · Vitest 5 + Testing Library + MSW 3 · Playwright + axe · Biome + ESLint · knip.
Node 24. Package manager: npm.

## Layout and import layers (enforced by ESLint — `npm run test:boundaries` proves it)

```
src/app/<route>/page.tsx      one-line re-export of a feature page; layouts; nothing else
src/features/<context>/       api · domain · entities · params · components · hooks · index.ts
src/shared/                   api (generated client) · config · lib · markdown · providers · sse · styles · ui · layout
```

- Layers: `app → features → shared`. A feature is imported **only through its `index.ts`**.
  A feature never imports another feature's internals. `shared` never imports `features`.
- Inside a feature: `entities.ts` = zod schemas + inferred types; `domain.ts` (or a `domain/` folder of small
  pure modules, as in `features/dd/`) = pure functions (no React, no fetch); `params.ts` = constants (no magic
  numbers/strings elsewhere); `hooks/` = TanStack Query wrappers; `components/` = UI. Copy the shape of
  `src/features/marketing/` — it is the reference implementation.
- Tests sit next to the file: `thing.ts` → `thing.test.ts(x)`. Pure `domain` code must have unit tests.
- Markdown is rendered only by `Markdown` from `@/shared/markdown` (GFM, math, alerts, highlight; KaTeX and
  mermaid load lazily). Pass `allowHtml` only for trusted-but-untidy chapter text — it adds the sanitiser. Never
  `dangerouslySetInnerHTML`. A feature that needs a tweak passes `remarkPlugins`/`components`, it does not fork.

## Code standards

- `import type` for types (verbatimModuleSyntax). No `any`, no `!` non-null assertions, no `console.*`.
  `noUncheckedIndexedAccess` is on: `array[i]` is `T | undefined` — handle it.
- Named exports only (except `page.tsx`/`layout.tsx`). Export only what another file uses (knip fails on unused).
- Server Components by default. Add `"use client"` only to files that use state, effects or browser APIs.
- Never call `setState` synchronously inside `useEffect` (lint error `react-hooks/set-state-in-effect`);
  derive values during render or use event handlers. Do not name any non-hook function `use…`.
- Data comes from the **generated client** only: `import { … } from "@/shared/api"`
  (`getXxxOptions`, `putXxxMutation`, …). Never hand-write `fetch("/api/...")`. The generated response types
  for most endpoints are open objects (`{[key: string]: unknown}`), so **parse every response with the zod
  schema in `entities.ts`** before using it; show a neutral error state if parsing fails.
- Errors from the API look like `{ "detail": "text" }` or `{ "detail": [{ "msg": "…" }] }` (FastAPI). The
  generated client throws that body: turn it into one readable line with `apiErrorMessage` from
  `@/shared/lib/api-error` (do not write another one). Never show a stack trace or raw JSON to the user.
  Type a mutation's error as `unknown`.
- Hooks that hold state for one resource (a slug, a thread) take it as a non-null argument and are mounted with
  `key={resource}` by the component that owns them — never reset state inside an effect when the id changes.
  Async work that outlives an event reads state through functional `setState`, not a render-time closure.
- Styling: Tailwind with the semantic tokens only (`bg-card`, `text-muted-foreground`, `border-border`…).
  Text colours are the `*-text` roles — `text-primary-text`, `text-success-text`, `text-warning-text`,
  `text-running-text`, `text-destructive-text`; **never** `text-primary` or `text-destructive` on text (those are
  fill colours and fail contrast), and a filled button uses `<Button>` (it pairs fill and label correctly).
  No hex or `oklch()` literals, no arbitrary `text-[11px]` sizes: **12px (`text-xs`) is the minimum**.
  Radius classes: `rounded-sm` controls, `rounded-md` surfaces. No shadows, gradients, glass or emoji.
  Read `DESIGN.md` before writing UI.
- Accessibility (WCAG 2.2 AA): every input has a visible `<label htmlFor>`; status text lives in an
  `aria-live="polite"` region; errors are linked with `aria-describedby`; never rely on colour alone; all controls
  reachable and operable by keyboard; target size ≥ 36px; buttons that start async work are disabled while
  pending and say what is happening ("Saving…").
- Secrets (API keys): **write-only**. Use `type="password"` and `autoComplete="new-password"`; never log, store
  (state beyond the input, localStorage, URL) or echo them back; clear the input after a successful save.

## Known traps in this repo

- `lucide-react` v1 has no brand icons (`Github` does not exist). Icons are 16px, only where they help scanning.
- A Server Component that only needs button classes imports `buttonVariants` from
  `@/shared/ui/button-variants`, never from `@/shared/ui/button` (that ships a client component).
- A CSS grid that collapses to one column on phones needs `grid-cols-1` (not implicit auto) or a long line widens
  the page.
- `src/shared/ui/**` is shadcn-managed. Add a component with `npx shadcn@4.22.1 add <name>`, then check
  `git diff --stat` shows only the new file(s) under `src/shared/ui/`; if `package.json` or other files changed,
  stop and report.
- MSW 3: `server.listen({ onUnhandledFrame: "error" })` (not `onUnhandledRequest`). In jsdom the client needs an
  absolute base URL: `client.setConfig({ baseUrl: "http://nexus.test" })` from `@/shared/api`.
- Run/thread ids contain slashes and **start with `docs-distiller/`** (`docs-distiller/<slug>/<uuid>`; synth:
  `docs-distiller/synth|study/<slug>/<uuid>`); the server finds a framework's runs by that prefix, so build them
  only with `generateThreadId`. The generated client sends them percent-encoded (`%2F`), which the backend
  decodes; in MSW match them with `*` and in Playwright with a single `*` segment.
- Planner/synth graph state is read **by key name** (`PLANNER_NODES`/`SYNTH_NODES` in `features/dd/domain/stages.ts`),
  and the spec types it as an open object, so a wrong key never fails — a node just never turns "done".
  `npm run contract:check` compares those tables with the backend's `NODE_ORDER` and `PlannerState`/`SynthState`;
  change both sides together. Planner state calls the scheduled nodes `next_nodes`, synth calls them `next`
  (`graphStateSchema` folds both).
- The stage graphs are React Flow (`@xyflow/react`, pinned 12.12.0 — not the 13 pre-release), and only
  `stage-node-board.tsx` imports `stage-flow.tsx`, through `next/dynamic`, so the library (~42 KB gz) stays out of
  every other page. Node positions are fixed (`domain/flow-layout.ts`; no dagre/ELK), so a stream event never
  re-lays-out. A node is a real `<button>` (`nodesFocusable={false}` avoids a second tab stop). React Flow sets
  `pointer-events: none` on nodes that cannot be dragged or selected, and children inherit it: the button
  re-enables it inline. The list view is the text equivalent; keep both. jsdom has no `ResizeObserver` — stubs
  are in `vitest.setup.ts`; edges only draw in a real browser, so test them in e2e.
- Planner and Synth have no tabs: they run side by side on the Pipeline tab. `/dd/planner` and `/dd/synth` remain
  as single-stage deep links (`DD_STAGES[].tab === false`).
- A side panel is `SideSheet` (native `<dialog>`, `features/dd/components/side-sheet.tsx`); a hook that opens
  one for a clicked item is `useSourceSheet`-shaped (state + handlers, no `useMemo`/`useCallback` in the page —
  the compiler lint rejects them there).
- Streams (SSE) are not in the OpenAPI spec: read them through `@/shared/sse`, with the URL built by
  `client.buildUrl` (see `features/dd/api.ts`), not a hand-typed string.
- A confirmation is the native `<dialog>` (`features/dd/components/overlays.tsx`), never `window.confirm`.
- `localStorage` is only for a per-browser preference (theme, auto-chain), never for ids the server already knows.
- A scrollable region (`overflow-x-auto`) needs `tabIndex={0}` plus a `biome-ignore` with the WCAG reason (see
  `shared/markdown/markdown.tsx`); and every new page gets its row in `e2e/` for axe, no sideways scroll and the
  12px floor. The one exception to the floor is KaTeX output (`.katex`): the e2e check skips it because math
  sub/superscripts are sized by the font, not by us.
- Mock data must look like real data. Tests with two short tiles passed while the real catalog (124 tiles,
  very long logo URLs) printed each URL as text and overflowed to 2383px. Fixtures carry long URLs and
  untagged code fences, and a page-level check asserts no sideways scroll. Remote logos are `<img alt="">`
  (see `catalog-tile.tsx`), never their address as text.
- An address param that one effect writes and another effect removes needs a one-way rule, or the two chase each
  other (the ingestion `?run=` flicker: recovery re-added a run the finish handler had just dropped, because the
  cached active-runs list was stale). Decide in a pure function (`runToAdopt`), remember what already finished,
  and test it with a harness where `router.replace` really changes the props. `invalidateQueries` cancels a
  refetch already in flight unless `{ cancelRefetch: false }`.
- The Ingestion sidebar mirrors the original docs: pages in **manifest order** (the backend's split keeps source
  order, audited for all 12 tier-1 docs), groups where their first page is, never sorted by name; a page is shown by
  its `title` (its heading; the backend fills it, `with_display_titles` covers older manifests), never its slug.
- A stage tab keeps `?slug=` (`StageNav` builds its links with `stageHref(stage, slug)`); an empty live
  region takes no height, so do not give status lines `min-h-*` — that is what left gaps in Settings.
- Usage screens (the node drawer's "By model" table and "top model", and the usage sheets) show the model ids the
  server reports, verbatim, because the owner asked for it (2026-10-28). Those names arrive as runtime data and are
  never written in source, copy or fixtures (`copy:check` guards the source; use neutral ids like `vendor-a/model-x`
  in tests). Anywhere else — marketing, home, settings copy — still no model or provider names.

## Commands (run from `apps/web/`)

| Command | Use |
| --- | --- |
| `npm run format` | Biome auto-format (run before the gates) |
| `npm run lint` | Biome + ESLint (must be clean) |
| `npm run typecheck` | `tsc --noEmit` (must be clean) |
| `npm test` | Vitest — all tests must pass |
| `npm run knip` | unused files/exports/deps (must be clean) |
| `npm run test:boundaries` / `copy:check` / `tokens:check` / `api:check` / `contract:check` | guards |
| `npm run verify` | all of the above — **the gate for every phase** |
| `npm run build && npm run budgets && npm run test:e2e` | production build, size budgets, browser tests |

A phase is **done only when its gate command exits 0**. Paste the last ~15 lines of the gate output in your
report. Never claim a gate passed without running it.

<!-- BEGIN:nextjs-agent-rules -->

## This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->
