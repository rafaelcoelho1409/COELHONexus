# COELHO Nexus — Frontend Stack

**Chosen: GOTTH + DaisyUI + Typography + Lucide (2026-04-24)**

| Layer | Tool | Why |
|---|---|---|
| Backend / server rendering | **Go** | Matches primary skillset; low-resource, single-binary; superb concurrency for long-running KD tasks |
| Templating | **[Templ](https://templ.guide)** | Type-checked HTML templates; drop-in replacement for `html/template`; editor LSP support |
| Interactivity | **[HTMX](https://htmx.org)** | Hypermedia-first, server-sent partial updates; no JS framework; LangGraph/KD progress streaming fits this pattern natively |
| Offline / installable | **PWA** (manifest + service worker) | Single-user self-host benefits from installable app shell + offline access to previously-synthesized chapters |
| CSS utilities | **[Tailwind CSS](https://tailwindcss.com)** (standalone binary — no Node) | Utility classes embed cleanly in `.templ` files; compile-time class validation via Templ; small purged bundle for PWA service-worker caching |
| Components layer | **[DaisyUI](https://daisyui.com)** | Semantic components (`btn`, `card`, `modal`, `tabs`) on top of Tailwind; 35 themes; swap theme via `data-theme` attribute |
| Prose rendering | **[@tailwindcss/typography](https://github.com/tailwindlabs/tailwindcss-typography)** (`prose` class) | Renders generated KD chapter markdown (headings, code blocks, lists) beautifully with one class |
| Icons | **[Lucide](https://lucide.dev)** (SVG sprites) | Modern dev-tool aesthetic; matches Linear / Vercel; PWA-cache-friendly |
| Code syntax highlighting | **[Shiki](https://shiki.matsu.io)** or **[highlight.js](https://highlightjs.org)** | For vaulted code fences in rendered chapters; re-initialize on `htmx:afterSwap` |
| Optional: keyboard shortcuts | **[hotkeys-js](https://github.com/jaywcjlove/hotkeys)** (2KB) | Command palette (Cmd+K) pattern |

**Full combo name: GOTTH + DaisyUI PWA**

(Go + Templ + Tailwind + HTMX + PWA + DaisyUI)

## Build toolchain

- `templ generate` — Go-native Templ compiler
- `tailwindcss` standalone binary — no Node.js required
- `air` or `reflex` — hot-reload in dev
- Skaffold syncs `**/*.go`, `**/*.templ`, `static/**` to the web container

## Design references

Primary inspiration (copy layout patterns):
1. **Memos** ([usememos.com](https://usememos.com)) — study-library aesthetic
2. **Stripe docs** ([stripe.com/docs](https://stripe.com/docs)) — chapter-reader layout
3. **Vercel deploy logs** — live-task monitor for 30-90 min KD runs

Secondary (developer-tool polish):
- Linear, Supabase, Fly.io, Tailwind docs

See [`KNOWLEDGE-DISTILLER-IMPROVEMENTS-ROADMAP.md`](KNOWLEDGE-DISTILLER-IMPROVEMENTS-ROADMAP.md) for backend context + why this stack aligns with the self-host, long-running-task, single-user profile.

## Why GOTTH specifically fits the COELHO Nexus author profile

(Based on [rafaelcoelho1409.github.io](https://rafaelcoelho1409.github.io) portfolio review 2026-04-24.)

The author is a Senior MLOps / ML Engineer with 6+ years of production-grade ML work, deep Kubernetes + Kafka + Spark + MLflow + Prometheus/Grafana + LangChain/LangGraph + Neo4j experience. Career career focus: Python, Go, Rust — deliberately outside the JS/TS ecosystem.

The GOTTH stack aligns with their existing infrastructure reality:

- **Kubernetes-native single binary** — deploys identically to their other Go/Rust services via Helm + ArgoCD
- **Prometheus-native metrics** via `prometheus/client_golang` — matches their 50+ metric / 11 dashboard operational baseline
- **HTMX SSE ↔ LangGraph streaming** — agent tool-call streams, KD Self-Refine iteration events, Celery task progress — all map to `hx-sse connect:` natively
- **DaisyUI `business` / `corporate` theme + @tailwindcss/typography** — matches the academic-minimalist aesthetic of their Jekyll/al-folio portfolio
- **No JS/TS cognitive tax** — career focus is Python + Go + Rust; adding React/Next would be a full language + ecosystem shift for near-zero benefit at the app classes they ship (dashboards, agent chat, data tables, long-task monitors)

The 5% of apps where HTMX struggles (heavy client-side canvas, real-time collaborative editing, Figma-class interactivity) are outside the author's project scope. For MLOps dashboards, KD observability, agent pipelines, knowledge-graph viewers, and content readers, GOTTH is a strict architectural win.

## Concrete first build targets (maps to existing KD screens)

1. **Study library** (landing) — Memos-style card grid of past KD runs; tags by framework; filters by date/status
2. **Chapter reader** — Stripe-docs layout: left nav (chapters) + main (`prose` rendering) + right rail (on-this-page). Vaulted code fences get Shiki highlighting on `htmx:afterSwap`
3. **Run monitor** — Vercel-deploy-logs clone: live log pane via `hx-sse` on `/studies/{id}/stream`; phase-indicator timeline (ingest → planner → synth → curator → critic → assembler); per-chapter grader trajectory sparklines
4. **Catalog health** — wrap `scripts/kd_catalog_health.py` as a Templ view: per-model success rate, cooldown state, recent failures. Renders the data you already have; replaces the CLI tool with a web dashboard
5. **Flashcards viewer** — Anki-style single-card flip, keyboard shortcuts (space = flip, 1-4 = grade)
