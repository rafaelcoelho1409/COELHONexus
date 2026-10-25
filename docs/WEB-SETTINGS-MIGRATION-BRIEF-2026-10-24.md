# Task brief — migrate the Settings page to `apps/web`

Audience: a coding agent (OpenCode). Reviewer: a stronger model, afterwards. Read `apps/web/AGENTS.md` and
`apps/web/DESIGN.md` first — their rules apply to everything below. Work in **phases**; do one phase per
request, stop at its gate, report, wait.

## Goal

Re-create the FastHTML page `/settings` (sections **LLM Endpoint** and **Embedding**) as a Next.js page at
`/settings`, following the project's code standards. Same behaviour, new design system. **Out of scope** (do not
build): "Source Tool Keys" section (belongs to a feature being removed) and the "Browse models" button/popover
(retired upstream).

Legacy files you may read for behaviour only (their comments contain stale notes — do not copy comments):
`apps/fasthtml/features/settings/page.py`, `apps/fasthtml/static/js/settings_endpoint.js`,
`apps/fasthtml/static/js/settings_embedding.js`.

## Allowed paths (everything else is forbidden)

```
apps/web/src/shared/ui/{brand-mark.tsx,input.tsx,label.tsx}          (brand-mark moved; input/label via shadcn)
apps/web/src/shared/layout/{app-shell.tsx,app-nav.tsx,nav.ts}        (new)
apps/web/src/app/(app)/layout.tsx                                    (new)
apps/web/src/app/(app)/settings/page.tsx                             (new)
apps/web/src/features/settings/**                                    (new)
apps/web/src/features/marketing/components/{site-header,site-footer}.tsx   (only the brand-mark import path)
apps/web/e2e/settings.spec.ts                                        (new)
```

## API contract (verified against the backend — trust this, do not re-derive it)

Base paths are same-origin; use the generated names from `@/shared/api`.

| Purpose | Generated name | Request | Response (200) |
| --- | --- | --- | --- |
| Read LLM endpoint | `getSettingsEndpointOptions()` | — | `ConnectionView` |
| Save LLM endpoint | `putSettingsEndpointMutation()` | body `{url, model, api_key?}` | `ConnectionView` |
| Test LLM endpoint | `postSettingsEndpointTestMutation()` | — | `LlmTestResult` |
| Read embedding | `getSettingsEmbeddingOptions()` | — | `ConnectionView` |
| Save embedding | `putSettingsEmbeddingMutation()` | body `{url, model, api_key?}` | `ConnectionView` |
| Test embedding | `postSettingsEmbeddingTestMutation()` | — | `EmbeddingTestResult` |
| Migration status | `getYcsContentEmbeddingMigrationStatusOptions()` | — | `MigrationStatus` |
| Start migration | `postYcsContentEmbeddingMigrationStartMutation()` | — | migration state (ignore body; refetch status) |
| Task progress | `getYcsAdminTaskTaskIdOptions({ path: { task_id } })` | — | `TaskStatus` |

`api_key` semantics (important): **omit the field** = keep the stored key; `""` = delete the key; non-empty =
replace it. `url` and `model` are always sent. Responses never contain the key — only `has_key`, `source`
(`"user"` = saved in the app, `"env"` = comes from deployment config), `last4`.
Errors: `400 {"detail": "…"}` (e.g. trying to change a key that is managed by the deployment), `404`/`503` on
migration start, `422 {"detail": [{"msg": "…"}]}`.

Shapes (write these **exactly** in `entities.ts`; zod 4):

```ts
export const keyStatusSchema = z.object({
  has_key: z.boolean(),
  source: z.enum(["user", "env"]).nullable(),
  last4: z.string().nullable(),
});
export const connectionViewSchema = keyStatusSchema.extend({ url: z.string(), model: z.string() });

export const llmTestSchema = z.discriminatedUnion("ok", [
  z.object({ ok: z.literal(true), reply: z.string(), latency_ms: z.number(), model: z.string().nullish() }),
  z.object({ ok: z.literal(false), error: z.string(), latency_ms: z.number() }),
]);
export const embeddingTestSchema = z.discriminatedUnion("ok", [
  z.object({ ok: z.literal(true), dimensions: z.number(), latency_ms: z.number(), model: z.string().nullish() }),
  z.object({ ok: z.literal(false), error: z.string(), latency_ms: z.number() }),
]);

const migrationModels = z.object({ from_model: z.string(), to_model: z.string() });
export const migrationStatusSchema = z.object({
  needed: z.boolean(),
  mismatch: migrationModels.nullable(),
  // null when no migration was ever started; otherwise the stored job state
  state: z
    .looseObject({
      status: z.string(), // "running" while a job is in flight
      from_model: z.string(),
      to_model: z.string(),
      task_id: z.string().optional(), // "" until the worker task exists
    })
    .nullable(),
  active_collection: z.string().nullable(),
});
export const taskStatusSchema = z.looseObject({
  task_id: z.string(),
  state: z.string(), // PENDING | STARTED | PROGRESS | SUCCESS | FAILURE | REVOKED
  meta: z.looseObject({ current: z.number().optional(), total: z.number().optional() }).optional(),
  error: z.string().optional(),
});
```

Export `type X = z.infer<typeof xSchema>` for each schema (`KeyStatus`, `ConnectionView`, `LlmTestResult`,
`EmbeddingTestResult`, `MigrationStatus`, `TaskStatus`). In `migrationView`, treat an empty `task_id` as "no task yet".

## Target structure

```
src/features/settings/
  index.ts                         export { SettingsPage } only
  entities.ts                      the schemas above + inferred types
  params.ts                        DEFAULT_MODEL="auto", MIGRATION_POLL_MS=3000, MIGRATION_RECHECK_MS=2000,
                                   TERMINAL_TASK_STATES=["SUCCESS","FAILURE","REVOKED"] as const
  domain.ts (+ domain.test.ts)     pure functions — see P1
  hooks/use-connection.ts          kind: "llm" | "embedding" → query + save + test (generated options/mutations)
  hooks/use-embedding-migration.ts status query, start mutation, task polling
  components/settings-page.tsx     server component: title, intro, <ConnectionCard kind="llm"/>, <ConnectionCard kind="embedding"/>
  components/connection-card.tsx   "use client": loads view, renders <ConnectionForm key=…/>
  components/connection-form.tsx   "use client": the form (inner component, state initialised from `initial`)
  components/key-status-pill.tsx   shows keyPillLabel(status)
  components/migration-banner.tsx  "use client": active-collection line + banner + button + progress
  + a .test.tsx next to each component
```

Form pattern (avoids setState-in-effect): `ConnectionCard` calls the query; when data exists it renders
`<ConnectionForm initial={view} key={`${view.url}|${view.model}|${view.has_key}|${view.last4}`} />`.
`ConnectionForm` holds `useState` for `url`, `model`, `apiKey`, `clearKey` initialised from `initial`. After a
successful save the new `view` changes the key, which remounts the form with a cleared key field. Do not
mirror props into state with an effect.

## Phases

### P0 — Orientation (no edits)

Read: `apps/web/AGENTS.md`, `DESIGN.md`, `src/features/marketing/**` (the reference slice), `src/shared/api/index.ts`,
`src/shared/ui/button-variants.ts`, `vitest.setup.ts`, `e2e/fixtures.ts`, and the three legacy files.
Reply with ≤ 15 lines: the files you will create, anything in this brief that looks wrong or ambiguous. **Stop.**

### P1 — Entities, params, domain (pure code + unit tests)

Create `entities.ts`, `params.ts`, `domain.ts`, `domain.test.ts`. `domain.ts` exports exactly:

- `keyPillLabel(status: KeyStatus): { tone: "custom" | "env" | "none"; text: string }` →
  `Custom key ••••abcd` / `Env key ••••abcd` / `No key` (missing `last4` → no digits, no stray dots).
- `validateBaseUrl(value: string): string | null` → error text unless the trimmed value parses as an `http:` or `https:` URL.
- `buildSavePayload(input: { url: string; model: string; apiKey: string; clearKey: boolean }): { url: string; model: string; api_key?: string }` →
  trims; empty model → `DEFAULT_MODEL`; `clearKey` → `api_key: ""`; non-blank `apiKey` → trimmed value; otherwise the `api_key` property is **absent** (not `undefined`).
- `errorMessage(error: unknown): string` → FastAPI `detail` string, or the first `msg` of a 422 list, else a generic "Request failed".
- `describeTest(kind: "llm" | "embedding", result: LlmTestResult | EmbeddingTestResult): { ok: boolean; text: string }` →
  LLM ok: `OK — 412 ms · model-id` (model part only if present); embedding ok: `OK — 1024 dimensions · model-id · 87 ms`;
  failure: `Failed: <error>`.
- `progressText(task: TaskStatus | undefined): string` → `3/10 transcripts re-embedded` when `state === "PROGRESS"` and `meta.total` is set (`current` defaults to 0); else `task.state`; else `Starting…`.
- `isTerminalTaskState(state: string | undefined): boolean`.
- `migrationView(status: MigrationStatus): { kind: "hidden" } | { kind: "needed"; from: string; to: string } | { kind: "running"; from: string; to: string; taskId: string | undefined }` →
  `running` when `state?.status === "running"`; else `needed` when `status.needed` and `mismatch` exists; else `hidden`.

Tests: at least 3 cases per function incl. edge cases (null `last4`, blank model, whitespace key, 422 list, no `meta`).
**Gate:** `npm run format && npm run verify`.

### P2 — App shell and the route

1. Move `src/features/marketing/components/brand-mark.tsx` to `src/shared/ui/brand-mark.tsx` (same content) and fix the two imports in marketing `site-header.tsx` / `site-footer.tsx` to `@/shared/ui/brand-mark`.
2. `npx shadcn@4.22.1 add input label` — then check `git diff --stat`: only `src/shared/ui/input.tsx` and `label.tsx` may be new; otherwise stop and report. Make sure both use semantic tokens and `rounded-sm` (adjust inside these two files if shadcn emitted other radii).
3. `src/shared/layout/nav.ts`: `export const APP_NAV = [{ label: "Settings", href: "/settings" }] as const;`
4. `src/shared/layout/app-nav.tsx` (`"use client"`): maps `APP_NAV` to `next/link` links; the link whose `href` matches `usePathname()` gets `aria-current="page"` and `text-foreground`, others `text-muted-foreground hover:text-foreground`.
5. `src/shared/layout/app-shell.tsx` (server component): sticky solid header like marketing's (`border-b border-border bg-background`, `max-w-6xl` container) with a link to `/` showing `BrandMark` + "COELHO Nexus", `<nav aria-label="Primary"><AppNav/></nav>`, `ThemeToggle`; then `<main id="main" className="mx-auto max-w-6xl px-4 py-10 sm:px-6">{children}</main>`.
6. `src/app/(app)/layout.tsx`: wraps children in `AppShell`. `src/app/(app)/settings/page.tsx`: `export { SettingsPage as default } from "@/features/settings";` plus `export const metadata = { title: "Settings — COELHO Nexus" };`. `features/settings/index.ts` and a placeholder `settings-page.tsx` rendering `<h1>Settings</h1>`.
**Gate:** `npm run format && npm run verify`, then `npm run build` (the route list must show `/settings`), then `npm run budgets` (still ok).

### P3 — Connection cards (LLM Endpoint + Embedding)

Implement `use-connection.ts`, `connection-card.tsx`, `connection-form.tsx`, `key-status-pill.tsx` and the real
`settings-page.tsx` (page title "Settings", one-sentence intro per card; each card is a `<section aria-labelledby>`).

Behaviour (parity with the legacy page):
- Fields: **Base URL** (text), **API key** (password; empty on load; `KeyStatusPill` beside it), **Model** (text). Buttons: **Save**, **Test**, and a small **Remove key** button shown only when `has_key && source === "user"`.
- Load: skeleton text "Loading…" while pending; on query error or zod failure show an inline error (`role="alert"`) with a **Retry** button.
- Save: validate with `validateBaseUrl` (show the error under the field, linked via `aria-describedby`, no request sent); else PUT `buildSavePayload(...)`; on success parse with `connectionViewSchema`, update the query cache with it (`queryClient.setQueryData`), status text "Saved." in an `aria-live="polite"` region; on error show `errorMessage(error)`.
- **Remove key**: PUT with `clearKey: true`; same success/error handling.
- Test: POST, parse with `llmTestSchema`/`embeddingTestSchema`, show `describeTest(...)` (success: `text-success-text`, failure: `text-primary-text` — always with the words OK/Failed, never colour alone). The test checks the **saved** configuration: while the form has unsaved changes show "Unsaved changes — Test uses the saved settings." next to the button.
- Pending states: buttons disabled and labelled "Saving…" / "Testing…".
- After a successful **embedding** save, invalidate the migration-status query (a model change can require a migration).
- The typed API key must never be rendered, logged, or kept after a save.

Tests (Testing Library + MSW; mirror `src/features/marketing/components/home-page.test.tsx` for setup): loads and fills
the fields; pill text for user/env/none; save with an empty key sends **no** `api_key`; save with a typed key sends it once and the field is empty afterwards; Remove key sends `""`; invalid URL blocks the request and shows the error; a 400 `detail` is displayed; Test success and failure texts; LLM and embedding cards hit different URLs.
**Gate:** `npm run format && npm run verify`.

### P4 — Embedding migration banner

Implement `use-embedding-migration.ts` and `migration-banner.tsx`, rendered inside the Embedding card below the form.
- Always show a read-only line "Active collection: `<name>`" (monospace) when `active_collection` is set.
- Banner by `migrationView(status)`: `hidden` → nothing; `needed` → text "Embedding model changed from **A** to **B**. Previously ingested videos won't be searchable until they are re-embedded." plus button **Migrate now** (POST start; pending label "Starting…"; on success invalidate the status query; on error show `errorMessage`); `running` → "Migrating A → B…" and a progress line from `progressText(task)`.
- Polling is TanStack-native: `useQuery({ ...getYcsAdminTaskTaskIdOptions({ path: { task_id } }), enabled: Boolean(taskId), refetchInterval: (query) => isTerminalTaskState(query.state.data?.state) ? false : MIGRATION_POLL_MS })`. Parse each response with `taskStatusSchema` (use `select`).
- When the task reaches `SUCCESS`: status text "Embedding migration complete"; on `FAILURE`/`REVOKED`: "Embedding migration failed — check the worker logs". In both cases invalidate the status query once, after `MIGRATION_RECHECK_MS` (use a `setTimeout` inside an effect that **only calls `invalidateQueries`**, never `setState`, and clears the timer on cleanup).
- Progress/status text is in an `aria-live="polite"` region.

Tests: hidden / needed / running render; Migrate now posts and refetches; progress `3/10 transcripts re-embedded` appears from a mocked `PROGRESS` task; terminal state stops polling (use fake timers: `vi.useFakeTimers()` + `vi.advanceTimersByTimeAsync`).
**Gate:** `npm run format && npm run verify`.

### P5 — Browser tests and final gate

Create `e2e/settings.spec.ts` (import `test`, `expect` from `./fixtures`; do **not** use the `api` fixture — register
`page.route("**/api/v1/…", …)` handlers yourself). Cover: page renders both cards with mocked data; saving sends the
expected JSON (capture `route.request().postDataJSON()`), key field empty afterwards; Test shows the result;
migration banner flow; axe WCAG 2.2 AA scan in dark and light (`new AxeBuilder({ page }).withTags([...])` as in
`e2e/home.spec.ts`); no horizontal scroll at phone width; keyboard-only completion of the LLM form.
**Gate:** `npm run verify:full` (it builds, checks budgets and runs every e2e test). Fix failures properly — never delete or skip a test.

## Final report (your last message; ≤ 25 lines)

1. Files created/changed (from `git status --short`, apps/web only).
2. Gate output tails for P5 (`verify` and `verify:full`).
3. Anything you could not do or decided differently, and why.
4. Anything in this brief that was wrong.
Do not commit. Do not push.
