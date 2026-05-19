# UI architecture — May 2026 SOTA (committed)

**Status:** design committed; implementation pending (5-day sprint).
Supersedes the vertical-card-list pattern in the current Planner/Synth
panels.

**Companion docs:**
- `PLANNER-ARCHITECTURE-2026-05-17.md` — backend pipeline this UI surfaces
- `SYNTH-ARCHITECTURE-SOTA-2026-05-18.md` — Synth stage architecture
- `KD-PIPELINE-SUBSTEP-MAP-2026-05-15.md` — substep enumeration

---

## TL;DR — three verdicts

| Question | Verdict |
|---|---|
| Overall multi-stage app UX in 2026 | **Stepper-as-navigation is still SOTA** — what changed is what's INSIDE each step (graph canvas + right drawer) |
| LangGraph nodes as a graph or a list? | **Render each LangGraph as an actual DAG canvas.** Use **Cytoscape.js** (vanilla JS, one CDN tag, ~10 nodes fits perfectly) |
| Side panel for per-node real-time updates? | **Click-node → right-drawer is the convergent 2026 pattern.** Generalize the existing file drawer into 3 zones (KPI strip + throttled log tail + collapsible inputs/outputs) |

**Stack stays pure FastHTML + vanilla JS + CDN scripts.** No React, no build
step, no framework swap. Cytoscape.js is the only new dependency, and it's
a single `<script>` tag.

---

## What 2026 production systems converge on

Two-pane "topology + detail" is the convergent pattern. Left/center =
where you are in the pipeline (a graph or a status list), right = what
THIS thing is doing right now.

| System | Macro nav | Per-stage canvas | Drill-down |
|---|---|---|---|
| **LangSmith Studio (2026)** | Tree of runs | LangGraph View (graph) | Right detail panel |
| **Claude Code Agent View (v2.1.140, May 2026)** | State-grouped list | Animated session rows | Peek panel (`Space` to open) |
| **Langfuse Agent Graphs (GA 2026)** | Trace list | Agent Graph view | Right observation panel |
| **Dagster (2026)** | Asset graph | Per-asset materialization graph + status colors | Side-by-side asset detail |
| **Temporal Web UI (2026)** | Workflow list | Compact + Timeline views | Right history panel |
| **OpenAI Agent Builder / AgentKit (2026)** | — | Canvas of typed nodes | Right config + run sidebar |
| **Arize Phoenix (2026)** | Trace list | Span-tree-first | Right detail panel |
| **Laminar (top of 2026 rankings)** | Project list | Transcript view | Right metric panel |

---

## Graph library choice — Cytoscape.js

### Comparison (constraints: vanilla JS, CDN, ~10 nodes/stage, live status, no build step)

| Library | Vanilla JS via CDN | Per-node live styling | Click handlers | Bundle | Fit |
|---|---|---|---|---|---|
| **Cytoscape.js** | ✓ cdnjs, one tag | ✓ Selector-based (`node[status='running']`) maps cleanly to your SSE events | ✓ Native | ~320 KB | **Best fit** |
| React Flow / xyflow | ✗ needs React build | ✓ | ✓ | heavy | Wrong stack |
| Mermaid.js 11/12 | ✓ | ✗ Re-parse on every status change → flicker | Awkward | ~600 KB | Cheapest if you want 70% of the win in 80 LOC |
| vis-network | ✓ | OK | OK | ~350 KB | Overkill for 10 nodes; opinionated drag UX |
| Sigma.js | ✓ | OK | OK | Lean | Designed for 10k+ nodes — mismatch |
| GoJS | Commercial license | ✓ | ✓ | Heavy | License problem |
| JointJS Community | ✓ | ✓ SVG | OK | Heavy | Overkill |
| Drawflow / Litegraph.js / Rete.js | ✓ | Editor-first | Limited | Lean | They are editors, not viewers |

**Why Cytoscape.js wins for COELHO Nexus specifically:**

1. One `<script src="https://cdnjs.cloudflare.com/ajax/libs/cytoscape/3.30.x/cytoscape.min.js">` tag, zero build step.
2. **Selector-based styling means a single `cy.getElementById('cluster').data('status', 'running')` triggers visual update** — this is *exactly* the model your `data-status` SSE events already produce. No paradigm shift in the SSE handler.
3. `breadthfirst` layout produces clean top-to-bottom DAGs for your 8-9-node graphs without needing `dagre`. (Add `cytoscape-dagre` only if you outgrow it.)
4. Built-in `.animate({...})` for the "pulse" running indicator.
5. Node click handlers map cleanly to the existing drawer code.

**Mermaid alternative if cheapest-path is needed:** a 50-LOC
`Mermaid.render()` of a `stateDiagram-v2` with `classDef` per status,
re-rendered on each "step done" SSE event, gets ~70% of the win in ~80
LOC. Downsides: (a) re-parse on every status change is wasteful and
flickers above ~2/s; (b) Mermaid's `classDef` doesn't support hover-
detail or per-node click handlers cleanly in 2026.

---

## Canvas UX (per stage, ~520 px tall)

### Visual spec

- **Nodes:** rounded 3px-radius rectangles, ~140×44 px, label centered, Raleway 13/16
- **Future (not yet implemented):** `#f5f5f5` fill, `#999` border, 40% opacity
- **Pending (implemented, not run):** white fill, gray border, full opacity
- **Running:** white fill, burgundy (`#c41230`) border, **pulse animation** (1.0 → 0.4 → 1.0 over 1.6s via CSS `@keyframes`)
- **Done:** white fill, green check icon, tiny gray latency pill bottom-right (e.g., "1.2s")
- **Failed:** white fill, thick burgundy border, small ✕
- **Edges:** 1px gray; **marching-ants animation** (stroke-dashoffset, 1.2s loop) only on the currently-active edge
- **Per-node KPI badge:** ONE number as a tiny secondary line under the label (e.g., "n=744" or "k=19") — drawer has the rest
- **Interaction:** click node → drawer opens; hover → passive tooltip (latency, node-type)
- **Top-of-stage status pill:** `✻ Working` / `✻ Needs input` / `✔ Completed` / `✕ Failed` — at-a-glance signal for the whole stage (this is what Claude Code Agent View leans on)

### Why click-to-drawer (not always-expanded, not hover-detail)

Click-to-drawer is the 2026 SOTA. LangSmith, Langfuse, Phoenix, Linear,
Dagster, Temporal — all use click. Hover-detail is reserved for passive
tooltips. Nothing should be always-expanded inside the canvas: **the
canvas IS the topology, the drawer IS the detail.**

---

## Drawer body — 3-zone layout (~420 px wide, reuse existing slide-in)

### Zone A — Status + KPI strip (sticky top, ~80 px)

Big status icon + node title; below, a 2-3 column inline KPI grid (e.g.,
"n_docs 744 | k_clusters 19 | latency 1.2s"). Updates in-place on every
SSE event for this node, throttled to one rAF batch (~16ms).

### Zone B — Live activity log (scrollable middle, fills remaining space)

Sticky-bottom auto-scrolling log of SSE events for this node, one line
per event:

```
▸ 14:22:01 umap_start n_docs=744
▸ 14:22:09 hdbscan_start reduced_dim=10
▸ 14:22:11 done n_clusters=19 n_noise=51 n_boundary=145
```

- Throttle inserts to a **100ms rAF batch** (the 2026 SSE-throttling production pattern)
- Cap DOM at ~200 lines, evicting from the top (cheap; avoids virtual-scroll complexity)
- Color severity: gray (info), burgundy (warnings), red (errors)
- **Sticky-to-bottom unless user scrolls up** (lock-when-scrolled pattern)
- **"Since last viewed" highlight** — subtle left-border on log entries that arrived since the drawer was last opened for this node

### Zone C — Collapsible sections (`<details>` native, 0 JS)

Default-collapsed, chevron-toggled — the Smashing Magazine **Thinking
Toggle** pattern:

- **Prompt / system** — the prompt template the node uses
- **Inputs** — JSON, syntax-highlighted, copy button
- **Outputs (latest)** — JSON or markdown, syntax-highlighted, copy button
- **Retries / errors** — only shown if non-zero

Plus a "View raw events" toggle at the bottom that switches Zone B to
raw JSON-line format (the LangSmith "raw run" affordance).

---

## SSE rendering technique (2026 production pattern)

- **One `EventSource` per stage** (already in place)
- Each event has `node_id` → route to per-node state and (if drawer is open and matches) to drawer
- **rAF-batched updates** — collect events in a queue, flush in `requestAnimationFrame`. This is the 2026 production answer for token streams and high-frequency status events. Prevents thrash, keeps 60fps at 50+ events/s.
- **DOM update via `data-status` attribute change** (NOT innerHTML rewrite); CSS handles the visual
- Drawer log: append-only; trim from top at 200 entries; CSS contains via `contain: strict` on the log container
- **Do NOT migrate to WebSockets** — SSE is the 2026 dominant transport for one-way LLM/agent streaming; CDN/proxy-friendly; auto-reconnects

---

## What to keep / change vs current implementation

### Keep (still SOTA in 2026)

- 5-step horizontal stepper (`Catalog → Ingestion → Planner → Synth → Study`) — still SOTA macro nav for fixed-stage pipelines
- Left sidebar (library list) — correct UX for single-user-with-history
- Sticky bottom action bar — matches Linear/Notion/Claude Code "command rail" pattern
- Right-anchored slide-in drawer — the convergent 2026 detail pane
- SSE over `EventSource` — 2026 default for AI streaming
- FastHTML server-rendering for static structure
- Vanilla JS for interactivity, `marked.js` for markdown
- Linear-style aesthetic (sharp 3px radii, burgundy accent `#c41230`, Raleway)

### Change

- **Planner Step 3 + Synth Step 4:** vertical card list → **Cytoscape.js DAG canvas**
- **Drawer body:** static KPI grid → **3-zone (KPI strip + log tail + collapsible inputs/outputs)**
- **SSE handler:** direct DOM writes → **rAF-batched, `data-status` as single source of truth**
- **Add top-of-stage status pill** (`✻ Working` / `✔ Completed` / `✕ Failed`)
- **Add "since last viewed" highlight** in drawer log

### Don't add

- React / Vue / Svelte
- HTMX
- WebSockets
- Mermaid (as primary — acceptable as cheapest-path alternative)
- Per-node always-expanded inline panels (the canvas + drawer model is strictly better)

---

## Implementation order (5-day sprint, ~480 LOC JS + ~180 CSS)

Stays within stated <500 JS / <200 CSS budget.

| Day | Work | LOC |
|---|---|---|
| **1** | Cytoscape integration scaffold | 120 JS + 60 CSS |
|     | • Add `<script src="cytoscape.min.js">` CDN tag in `shell.py` HEAD | |
|     | • Factor `PlannerGraph` module in `docs_distiller.js`: `init(containerEl, nodes, edges)`, `setStatus(nodeId, status, kpis)`, `onNodeClick(handler)` | |
|     | • Replace `#fw-planner-cards` render with `#fw-planner-canvas` (keep cards behind feature flag for one release) | |
|     | • Style: nodes 140×44 px, breadthfirst layout, classes `.pending .running .done .failed .future`, CSS keyframe pulse for `.running` | |
| **2** | Wire SSE → graph | 80 JS |
|     | • Replace per-card status update in `applyEvent` with `PlannerGraph.setStatus(node_id, status, kpis)` | |
|     | • Add rAF-batched event queue (push to `pending[]`, flush in `requestAnimationFrame`) | |
|     | • Verify existing replay path (`_tryResumeActivePlanner`) hydrates the graph correctly | |
| **3** | Drawer body redesign | 140 JS + 80 CSS |
|     | • Generalize existing file drawer to `NodeDrawer.open({nodeId, title, kpis, eventStream})` | |
|     | • Build Zone A (sticky KPI strip), Zone B (log tail with sticky-bottom + 200-line cap), Zone C (collapsible sections via `<details>`) | |
|     | • Route SSE events to drawer only if `drawer.openNodeId === event.node_id` | |
| **4** | Polish + status pill | 60 JS + 40 CSS |
|     | • Stage-level status pill above canvas | |
|     | • "Since last viewed" highlight via stored `lastSeenAt[nodeId]` per session | |
|     | • Animated edge for currently-active transition (CSS stroke-dashoffset) | |
|     | • Empty-state placeholders for pre-run and post-wipe | |
| **5** | Synth parity + cleanup | 80 JS |
|     | • Apply same canvas/drawer to Step 4 Synth (9 nodes, has fan-out — breadthfirst handles fine) | |
|     | • Delete legacy card-render code once graph path is stable | |
|     | • A11y: tab-cycle through nodes, ARIA labels on icons (via parallel hidden `<ul>` mirror — Cytoscape renders to canvas, not SVG, so a11y needs a mirror) | |

**Total: ~480 JS + ~180 CSS** (under 500/200 cap). Drops to ~350 JS if
existing drawer reused mostly as-is with only inner body rebuilt.

---

## Files touched

- `apps/fasthtml/shell.py` — add Cytoscape CDN `<script>` tag in HEAD
- `apps/fasthtml/features/docs_distiller.py` — swap `#fw-planner-cards` for `#fw-planner-canvas` in Planner panel; same for Synth panel; redesign drawer body markup
- `apps/fasthtml/static/js/docs_distiller.js` — new `PlannerGraph` + `SynthGraph` + `NodeDrawer` modules; replace card render path (~lines 1385-1880); reuse drawer infrastructure (~lines 51-57)
- `apps/fasthtml/static/css/app.css` — new `.fw-planner-canvas`, `.fw-drawer-zone-*`, `@keyframes pulse`, `@keyframes ants` rules

No backend changes required. No new dependencies beyond Cytoscape CDN.

---

## Key sources (May 2026)

- [Claude Code Agent View docs (v2.1.140)](https://code.claude.com/docs/en/agent-view)
- [LangSmith Studio + LangGraph View](https://docs.langchain.com/langsmith/studio)
- [Langfuse Agent Graphs (GA 2026)](https://langfuse.com/docs/observability/features/agent-graphs)
- [Laminar 2026 observability ranking](https://laminar.sh/article/2026-04-23-top-6-agent-observability-platforms)
- [Smashing Magazine — Practical Interface Patterns For AI Transparency (May 2026)](https://www.smashingmagazine.com/2026/05/practical-interface-patterns-ai-transparency/)
- [Smashing Magazine — Designing For Agentic AI (Feb 2026)](https://www.smashingmagazine.com/2026/02/designing-agentic-ai-practical-ux-patterns/)
- [Cytoscape vs vis-network vs Sigma 2026](https://www.pkgpulse.com/blog/cytoscape-vs-vis-network-vs-sigma-graph-visualization-javascript-2026)
- [xyflow / React Flow](https://reactflow.dev) (rejected — React-only)
- [Mermaid `stateDiagram-v2` docs](https://mermaid.js.org/syntax/stateDiagram.html) (acceptable as cheapest-path alternative)
- [Temporal UI 2026 (Compact + Timeline)](https://docs.temporal.io/web-ui)
- [Dagster asset graph + side-by-side detail](https://docs.dagster.io/guides/operate/webserver)
- [Arize Phoenix tracing UI](https://arize.com/docs/phoenix/tracing/llm-traces)
- [SSE 2026 production patterns](https://thebackenddevelopers.substack.com/p/server-sent-events-in-2026-streaming)
- [Cytoscape.js docs](https://js.cytoscape.org/)

---

## Tradeoffs accepted

- **Canvas vs SVG (a11y):** Cytoscape renders to `<canvas>` not SVG. Screen readers can't see node labels natively — need to ship a parallel hidden `<ul>` mirror (Day 4 a11y pass). React Flow handles a11y better out-of-box but requires React. For single-user dev tooling, canvas-vs-a11y mirror is the standard 2026 compromise.
- **One new CDN dependency:** Cytoscape.js (320 KB minified). No build step, no npm; cached aggressively by browsers.
- **Card view kept behind feature flag for one release** so a regression rollback is one config change, not a revert PR.

## Footnotes — what's NOT in scope (deferred)

- Multi-user / multi-tenant UI chrome (not needed; single-user app)
- Auth UI (not needed)
- Mobile responsive (single-user desktop dashboard, mobile not a use case)
- Dark mode (deferred — light Linear-style is the design choice)
- Cytoscape extensions (`cytoscape-dagre`, `cytoscape-popper`) — add only if `breadthfirst` layout proves insufficient at 9+ nodes
- WebSocket bidirectional UI (intentionally rejected — SSE one-way is the 2026 SOTA for AI streaming UIs)
- HTMX (intentionally rejected — vanilla JS + targeted DOM updates work fine for this scale)
