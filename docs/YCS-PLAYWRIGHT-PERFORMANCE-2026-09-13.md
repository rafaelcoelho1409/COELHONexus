# YCS Playwright pipeline — speed pass, live-debug tooling, and open architecture questions (2026-09-13)

Context: Phase 1 (Playwright + ElasticSearch) extraction worked correctly
but was slow, memory-heavy, and occasionally captcha'd. This doc covers
the SOTA research done before touching code, everything actually shipped
and measured today, the live-debugging tooling now available for future
maintenance, and two open architecture questions raised but not yet
implemented.

## Alternative-browser evaluation — Obscura, rejected

`/sota-search` was run on whether a lightweight non-Chromium browser
could replace Playwright+CDP for speed/RAM/captcha reasons. Findings:

- **Patchright** (patched Playwright, real Chromium, drop-in
  `connect_over_cdp`) is the correct low-risk lever for captcha
  specifically — not applied yet, captcha has only fired once all
  session and wasn't prioritized.
- **Obscura** (github.com/h4ckf0r0day/obscura) — Rust-native rendering
  engine, no Chromium — was inspiration for Cloudflare's Kitesurf
  (confirmed via Cloudflare's own blog: *"We got the initial inspiration
  from obscura"* — conceptual only, Kitesurf's real implementation is
  Blitz+Stylo+Boa, none of it Obscura's code). Live-tested against the
  real CDP pod: connection succeeded, context pool warmed, but the
  actual `get_panel` fetch failed 100% of the time with
  `Page.evaluate: TypeError: Cannot read properties of undefined
  (reading 'evaluate')` — a Playwright-driver-internal error, meaning
  Obscura's CDP implementation doesn't fully replicate the
  execution-context lifecycle Playwright's client library depends on.
  **Verdict: not viable for this stack. Do not revisit without new
  compatibility evidence from the Obscura project itself.**
- Cloudflare Kitesurf (the thing Obscura inspired) is real and live in
  beta but Cloudflare-Workers-only, not open-sourced, not self-hostable
  — excluded per the project's free/self-hosted-only rule.

## Shipped changes, in order applied

All in `apps/fastapi/domains/ycs/transcript/{params,service}.py` unless
noted.

### 1. Resource blocklist expansion

Root cause found via live network capture through `chrome-devtools-mcp`
(see tooling section below) on an **unblocked** baseline page: DCL fired
at ~2.56s, main HTML decoded to 1.27MB, 185 requests total. Already
blocked: video streams, ad domains, `api/stats/*`, thumbnails,
image/font resource types. Newly blocked, all confirmed safe by
inspecting the live DOM (`ytcfg.set`/`ytInitialPlayerResponse` are set
by **inline** `<script>` tags, fully independent of any external
`<script src>` — verified via `document.querySelectorAll('script')`
before blocking anything):

- `fonts.googleapis.com/*`, `fonts.gstatic.com/*` (webfont CSS + ~70
  icon SVG requests seen firing unblocked)
- `google.com/pagead/**`, `google.com.br/pagead/**` (ad-conversion
  pixels the narrower existing `**/pagead*` pattern didn't reach —
  single-`*` glob tails don't cross further `/` segments)
- `cast_sender.js` (Chromecast SDK, no cast targets on a datacenter box)
- 7 peripheral player-feature JS bundles: `captions.js`,
  `miniplayer.js`, `endscreen.js`, `annotations_module.js`, `remote.js`,
  `offline.js`, `lottie-light.vflset/**` — verified independent of the
  inline bootstrap scripts before blocking
- `BLOCK_RESOURCE_TYPES` gained `"stylesheet"` — CSS/layout is never
  read by the fetch-based extraction paths

### 2. Navigation lifecycle: `domcontentloaded` → `commit`

`page.goto(..., wait_until="commit")` — returns as soon as response
headers are parsed, before HTML parse. Safe because the actual gates
(`_get_player_state`, `_wait_for_innertube_context`) already re-poll for
the real data via `wait_for_function` regardless of what `goto()`
waited for; the only effect is that Python's polling starts sooner,
closing the dead time between commit and DCL.

### 3. Parallelized the two independent global-waits

`_get_player_state` (up to 8s for `ytInitialPlayerResponse`) and
`_wait_for_innertube_context` (up to 3s for `ytcfg`) ran **sequentially**
— worst case ~11s combined, checking two unrelated `window` globals set
by separate inline scripts. Now run concurrently via
`asyncio.ensure_future`; caps worst-case wait at `max(8s, 3s)`. The
unplayable/no-caption-tracks short-circuits still cancel the innertube
task properly (`.cancel()` + `await` inside `except CancelledError` to
avoid orphaned tasks) rather than consuming it.

### 4. Merged two `page.evaluate()` calls into one

`_get_player_state` and the old standalone `_get_caption_tracks` both
read `window.ytInitialPlayerResponse` via two separate CDP
`Runtime.evaluate` round-trips. `_get_caption_tracks` deleted; its logic
folded into `_get_player_state`'s existing evaluate call. One fewer
round-trip per video.

### 5. Wired up `BROWSER_REFRESH_INTERVAL` (was dead code, not a cleanup — a real bug)

`browser_refresh_interval` was threaded all the way through
`__init__`/`init_transcript_service`, and `extract/task.py` **explicitly
overrides it to `10`** at all three call sites — clear evidence of real
original intent — but nothing ever compared `_videos_since_refresh`
against the threshold. Only the reactive path
(`_ensure_healthy_browser`, triggered by a CDP health-check failure)
ever refreshed. Added `_maybe_refresh_browser()`, called from
`_fetch_single_attempt` right after `_ensure_healthy_browser()`, using
the same drain-active-ops-then-refresh pattern as the existing reactive
path. Confirmed live: fires cleanly every 10 videos, CDP
reconnect+rewarm costs ~150ms, zero dropped work.

### 6. Concurrency trial: `MAX_CONCURRENT`/`CONTEXT_POOL_SIZE` 5 → 6

Per-page memory footprint should be lower post-blocklist-expansion, so
there may be headroom above the historical "5 concurrent pages OOMKilled
at 2Gi" ceiling (current pod limit is 4Gi). **Shipped but not yet
live-monitored** — `kubectl top pod -n playwright --containers` should
be watched during a real batch before treating this as final; revert to
5/5 if chromium's RSS creeps toward the 4Gi limit. Idle baseline
recorded before the bump: chromium container ~900Mi (residual from a
prior batch, not a clean idle number).

## Measured results

Same 5 Raiam Santos McArn videos, real fetches (no cache), across the
session:

| Stage | `ACxhc2wFH2g` individual | 2-video batch total | 2-video batch avg |
|---|---|---|---|
| Before any change | 8.72s | 10.4s | 5.2s/video |
| After commit+blocklist (items 1-2) | 5.99s | 7.4s | 3.7s/video |

**-31% / -29% / -29%** respectively — the single biggest lever.

25-video Raiam Santos McArn batch (fresh, no cache), before vs. after
items 3-5 (parallel waits + merged evaluate + proactive refresh), on top
of items 1-2 already applied:

| Metric | Before 3-5 | After 3-5 | Δ |
|---|---|---|---|
| Total task | 59.8s | 58.2s | -2.7% |
| Transcript-fetch total (sum of chunk batches) | 40.7s | 37.0s | **-9%** |

Both 25-video runs: 24/24 real transcripts fetched, 0 `fetch_failed`, 0
errors, same 1 legitimate no-caption-tracks video
(`O5pmO8qCv6s`) both times. Verified directly against Elasticsearch
(not just logs) both times — all 24 documents present, content length
7.5K-19.5K chars, no empty/truncated content, missing-video-ID check
returned empty both times.

Net: **~35-40% faster** end-to-end on the fully-stacked change set vs.
the pre-optimization baseline, zero correctness regressions across two
independent 25-video validation runs plus the isolated 2-video A/B pair.

## Live-debugging tooling now available

Set up for future maintenance whenever YouTube changes something and the
extraction paths need live diagnosis instead of log-guessing.

**Permanent CDP endpoint** (no `kubectl port-forward` needed — this is a
standing Tailscale Ingress in the `playwright` namespace, survives
redeploys):

```
https://playwright-cdp.tail39dc94.ts.net
```

`chrome-devtools-mcp` is the right tool for this (not `@playwright/mcp`)
— its tool surface is a strict superset for this use case: full network
inspection (`get_network_request`/`list_network_requests`, including
response bodies), console (`list_console_messages`), script evaluation
(`evaluate_script`), AND full input-automation (`click`/`fill`/
`type_text`/`navigate_page`/`wait_for`) in one server. Playwright MCP's
only real differentiator (Firefox/WebKit support) is irrelevant — YCS
only ever targets Chromium via CDP.

Permanent install:

```bash
npm install -g chrome-devtools-mcp
claude mcp add chrome-devtools --scope user -- chrome-devtools-mcp --browserUrl=https://playwright-cdp.tail39dc94.ts.net
```

**Gotcha hit and fixed**: the CDP `/json/version` response's
`webSocketDebuggerUrl` comes back as `ws://playwright-cdp.tail39dc94.ts.net/devtools/browser/<id>`
with **no port** — defaults to port 80, but the Tailscale ingress only
serves 443. `--browserUrl` trusts this raw value and fails
(`ECONNREFUSED ...:80`). Fix: register with `--wsEndpoint` using the
corrected `wss://` scheme and the browser id from `/json/version`
instead of `--browserUrl`. This is the exact same class of bug
`domain.py`'s `_get_cdp_websocket_url()` was written to work around for
YCS's own Python code — `chrome-devtools-mcp` doesn't have that
reconstruction logic built in.

**Safety note**: this is the *production* headed-Chromium pod — any
live MCP debugging session shares the same browser/context pool the
real pipeline uses. Check Flower/Ingestion page for an in-flight run
before connecting interactively; use `isolatedContext` on `new_page` to
avoid polluting the pooled contexts.

**noVNC viewing** (real Chromium pod, not Obscura — Obscura has no
noVNC, it's headless-only with no window/framebuffer to stream):
`https://playwright-vnc.tail39dc94.ts.net` — was re-prompting for the
VNC password on every dropped WebSocket connection (VNC auth is
inherently per-connection, no session-length setting exists anywhere in
`x11vnc`, the Tailscale operator, or the `ProxyClass` CRD — checked the
CRD's actual generated API reference, confirmed no idle-timeout field
exists at all). Fixed via noVNC's own `reconnect=true&reconnect_delay=3000`
URL params instead (auto-retries silently using the same page session's
credentials); homepage tile URL updated in
`~/COELHOCloud/infrastructure/modules/playwright/k8s/ingress-novnc.yaml.tpl`
(separate repo, apply via `terragrunt apply` when ready).

## Open architecture questions — discussed, not yet implemented

### A. Split the Ingestion-page phase bars 1-4 (Playwright / ElasticSearch / Qdrant / Neo4j)

Currently one combined "Phase 1 · Playwright & ElasticSearch" bar.
Checked the actual progress-callback code
(`domains/ycs/extract/task.py`, `domains/ycs/transcript/service.py`):
the existing tick signal is **already Playwright-only** under the hood
— `fetch_batch`'s `on_video_done` fires the instant Playwright resolves
each video, before that chunk is even sent to ES. **ES-transcript-
indexing currently has zero progress signal** — it happens silently,
per-chunk, after each Playwright chunk resolves.

- Splitting out Phase 1 (Playwright) is free — relabel the existing bar.
- Making Phase 2 (ElasticSearch) a real, visible bar needs one new
  progress-callback hookup around `index_transcriptions_to_elasticsearch()`
  inside `fetch_transcriptions_batch` — small, scoped, not a rewrite.
- Caveat: that bar will tick in chunk-sized jumps (0→10→20→24), not
  smooth per-video like Playwright's — decide upfront whether that
  reads as intentional (bulk-write) or looks broken next to a smooth
  bar, before shipping.
- Renumbering Qdrant/Neo4j to Phase 3/4 is a pure label change on top.

### B. Neo4j's LLM chain bypasses the shared, tuned rotator client

Traced the call path in `domains/llm/rotator/chain/service.py`:

- DD's hot-path nodes (`doc_distill`/`off_topic`/`chapter_assign`) use
  `_get_async_openai()` — pooled `AsyncOpenAI`, HTTP/2, 200/100
  connections, tuned 90s timeout ceiling, `max_retries=0` (comment:
  *"rotator handles cascade, SDK retries would triple timeout"*).
- YCS's Neo4j chain (`build_ycs_neo4j_pinned_chain` →
  `build_reduce_label_chain()`, same file, line ~642) builds a **bare**
  `ChatOpenAI(base_url=..., api_key=..., model=..., temperature=0.0)` —
  no timeout override, no `max_retries` override, no connection
  pooling, a fresh instance per call.

This means Neo4j's calls fall back to the openai-python SDK's own
defaults (600s timeout, `max_retries=2`) — the exact "SDK retries would
triple timeout" scenario the neighboring function's comment warns
against, just currently unapplied to this one call site. Real,
plausible contributor to Neo4j being the slowest, most timeout-prone
stage.

**Fix scope**: route `build_reduce_label_chain()`/
`build_ycs_neo4j_pinned_chain()` through the same pooled client (or at
minimum pass `timeout=90.0, max_retries=0` explicitly to the
`ChatOpenAI` construction) — small, targeted, not a rewrite.

**Not a "move DD's engine into the rotator" question** — the shared
client already lives at the Nexus-app level
(`domains/llm/rotator/chain/service.py`, not under `domains/dd/`), so
there's nothing to transfer between repos. The rotator service itself
already owns the provider-cascade/arm-swap logic server-side. What
can't move server-side is the client's own outer-bound timeout/retry
count — different consumers have genuinely different budgets (a Celery
task with a 3660s hard `time_limit` vs. a synchronous request path) —
that has to stay a per-consumer setting, which is exactly what already
exists and just needs to be actually used here.

## Not done, explicitly deferred

- Patchright swap (captcha mitigation) — not prioritized, captcha fired
  once all session and self-resolved.
- Item 6 (concurrency 5→6) needs a live-monitored batch run before being
  treated as final.
- Phase-split UI (A) and Neo4j client fix (B) above — discussed, no code
  changes made yet, pending a decision on which to do first.
