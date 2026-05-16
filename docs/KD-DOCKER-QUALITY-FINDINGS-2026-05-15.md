# KD Docker Quality Findings (2026-05-15)

**Subject:** quality audit of the materials written to MinIO `coelhonexus/default/knowledge/docker-latest-senior/` by canary v10 on 2026-05-14 (the Docker run that hit Celery's 2h `task_soft_time_limit=7080` ceiling). Pairs with the speed plan in `docs/KD-SPEED-OPTIMIZATION-PLAN-2026-05-14.md` and the v7–v10 canary log in `docs/KD-CANARY-V7-V10-FINDINGS-2026-05-14.md`. Where those two cover *speed* and *architecture*, this one covers **content quality** + the **code-mapped pollution sources**.

**Top-line verdict:** materials are **not usable as a Docker reference**. The architecture wrote *something* end-to-end, but the prose ships with structural pollution, mid-command truncation, large-scale heading duplication, mis-routed content, and 3 of 10 planned chapters missing entirely.

**Headline counts (across 5 chapters audited: ch01, ch02, ch03, ch05, ch08):**
- **3 of 10 planned chapters missing** (ch06, ch09, ch10 — none written to S3)
- **274** `# ...(truncated)` markers inside code fences → shell commands unrunnable
- **630** `# docs: 0NNN-...md` source-ID lines leaking into prose
- **267** orphan 12-char hex hashes on their own line
- **88** unresolved `<code-ref hash="…"/>` placeholders in ch08 alone
- **6×–7×** same H2 emitted within a single chapter (ch03, ch05, ch08)
- **1 chapter mis-routed**: ch02 title is "Docker Account Management"; body is Linux install procedures + MCP CLI
- **1 chapter stub-only**: ch03 challenges (273 B / 4 lines) + flashcards (690 B / 17 lines) are template placeholders
- Average narrative coherence (5-chapter sample): **2.8 / 10**

---

## 1. MinIO inventory — what shipped

Source artifact files cached locally at `/home/rafaelcoelho/minio-data/kd-audit/` (downloaded via aistor MCP, 2026-05-15).

### Chapter directories present under `default/knowledge/docker-latest-senior/`

| Ch | README.md (B) | challenges.md (B) | flashcards.json (B) | mtime (UTC) | Anomalies |
|----|---:|---:|---:|---|---|
| 01 | 61,174 | 1,225 | 2,686 | 2026-05-14 23:10:13 | normal-shape |
| 02 | 13,164 | **missing** | **missing** | 2026-05-14 23:12:07 | abnormally small README; mis-routed content |
| 03 | 90,305 | **273** | **690** | 2026-05-14 22:43:54 | thin downstream artifacts (template stubs) |
| 04 | 75,211 | 3,598 | **461** | 2026-05-14 22:33:30 | tiny flashcards |
| 05 | 132,790 | 2,420 | 4,440 | 2026-05-14 23:26:11 | largest of complete; OK |
| **06** | **MISSING** | **MISSING** | **MISSING** | — | **entire chapter never written** |
| 07 | 94,089 | 2,982 | 4,022 | 2026-05-14 23:44:48 | normal-shape |
| 08 | 140,402 | 2,241 | 4,654 | 2026-05-14 23:58:22 | latest mtime; ends mid-paragraph (truncated at task SIGTERM) |
| **09** | **MISSING** | **MISSING** | **MISSING** | — | **entire chapter never written** |
| 10 | **MISSING** | **MISSING** | **MISSING** | — | **entire chapter never written** |

Total chapter-artifact volume: **640,112 B (~625 KB)** across 21 files (vs ~30 expected for a complete run).

### Research artifacts (all present, completed cleanly)
- `research/plan.json` — 73,956 B, mtime 22:05:18 → planner enumerated **10 chapters** with title + assigned_files lists
- `research/manifest.json` — 261,263 B, mtime 22:03:35
- `research/raw/` — 1,341 raw scrape files (~6 MB), mtime 22:01:46–22:03:05 → research phase ran in **~80 s**

### What `plan.json` asked for vs what was produced

| Ch | Plan title | assigned_files | Written? |
|---|---|---:|---|
| 1 | Desktop Setup & Advanced Features | 62 | ✅ |
| 2 | Docker Account Management | 71 | ⚠️ partial (mis-routed) |
| 3 | Docker Configuration & Management | 86 | ⚠️ stubs on challenges/cards |
| 4 | Docker Image Management | 47 | ✅ |
| 5 | Dockerizing Applications & Testing Workflows | 90 | ✅ |
| 6 | **Docker Build Configuration** | **138** | **❌** |
| 7 | Docker Compose Configuration & Deployment | 109 | ✅ |
| 8 | Docker AI Agents & Automation | 179 | ⚠️ ends mid-paragraph |
| 9 | **Docker Operations & Observability** | **105** | **❌** |
| 10 | **Hardened Docker Images & Security** | **59** | **❌** |

The three missing chapters cluster on **high-file-count chapters** (06: 138 files, 09: 105) AND tail-position. Combined with ch08's truncated terminal section, this is the canonical Celery soft-limit symptom: the chapter loop ran out of wall-clock before reaching the largest later-numbered chapters.

---

## 2. Content-quality findings (5-chapter sample)

### 2.1 Narrative coherence (subjective 0–10 per chapter)

| Ch | Score | First H2 | Last H2 | Note |
|----|---:|---|---|---|
| 01 | 2/10 | `## Step 4: Build the Next.js application image — Part 1 of 3` | `## Supported configurations` | Opens mid-tutorial (no Step 1–3 anywhere); section order is essentially random |
| 02 | 4/10 | `## Sign in to Docker Desktop` | `## Known Issues and Troubleshooting` | Sensible opener but body is Linux package installs — wrong topic for the chapter title |
| 03 | 1/10 | `## DNS resolver issues` | `## systemd unit file` | Same H2 repeated 6× (`repository:shortid`), 3× (`systemd unit file`); reads like the bandit re-pinned the same chunk and assembled it without dedup |
| 05 | 4/10 | `## Create a Kubernetes YAML file — Part 1 of 4` | `## Explore the application environment` | "Initialize Docker assets" emitted as Parts 2/3/4/5/6/7 of 7 with no Part 1 |
| 08 | 3/10 | `## Why doesn't the sandbox use my user-level agent` | `## Visual Studio Code {#vscode}` | Chapter ends mid-prose with no closing newline; "Base image" 3×, "Visual Studio Code" 2× |

### 2.2 Pollution categories — what we found and where

| Artifact | Count | Example (file:line) | Quote |
|---|---:|---|---|
| `# ...(truncated)` inside fenced code blocks | 274 | ch01:30 | `$ sudo apt install ./docker-desktop-amd64.deb # ...(truncated)` |
| `# docs: 0NNN-docs-docker-com-...md` inline citations | 630 | ch01:7,10,13 | `# docs: 0231-docs-docker-com-desktop-setup-install-linux-ubuntu-md` |
| Orphan 12-char hex on its own line | 267 | ch01:8 | `85f67f4492f8` (no surrounding context — looks like an image ID dropped between paragraphs) |
| Unresolved `<code-ref hash="…"/>` | 88 (ch08 alone) | ch08:266 | `<code-ref hash="6d5b96e2bad4"/>` — should have been substituted with the indexed code block, wasn't |
| `<file_slug>` literal placeholder | several | ch08:202 | `# docs: <file_slug>` followed by `0033-docs-docker-com-ai-...` — prompt template variable never substituted |
| Duplicated H2 sections | 17+ across ch03/ch05/ch08 | ch03:436,582,627,702,919,962,1004 | Seven sections titled `## `repository:shortid` image references` |
| Verbatim paragraph duplication | scattered | ch01:97 vs 101 | Same "Start the container in detached mode…" appears back-to-back |
| Stub-placeholder challenges/flashcards | 1 chapter (ch03) | ch03-challenges.md | `1. Explain the key concept covered in this chapter.` |
| Hallucinated facts in flashcards | ~3 in ch08 | ch08-flashcards.json | `<500ms startup`, `cosine ≥ 0.75` — neither appears in chapter body |
| Mis-routed content vs chapter title | 1 chapter (ch02) | ch02-README.md whole-file | Title "Docker Account Management", body is Debian/Fedora install + MCP CLI |

### 2.3 Per-chapter usability call

- **Ch01 Desktop Setup** — *partially usable.* Multi-stage Dockerfiles and nginx config blocks are accurate and runnable; everything else is noise. Skip the prose, copy the Dockerfiles.
- **Ch02 Account Management** — *not usable.* Content does not match title. The one on-topic section ("Sign in to Docker Desktop") is correct but truncated. Also missing challenges/flashcards entirely.
- **Ch03 Configuration & Management** — *not usable.* Same H2 emitted 6×; challenges + flashcards are template stubs.
- **Ch05 Dockerizing Applications** — *partially usable.* Longest, most synthetic. Postgres+FastAPI deployment YAML and Compose interpolation prose are genuinely good. "Initialize Docker assets" duplication wastes ~30% of the chapter.
- **Ch08 AI Agents & Automation** — *partially usable.* Best-conceived chapter. Fatal flaws: 81 unresolved `<code-ref/>` tags and the final section ends mid-prose (timeout truncation).

---

## 3. Why each pollution class happens (code-mapped)

All locations verified against current source as of commit `c27f47f`.

### 3.1 `# docs: <file_slug>` citation leakage — **by design**, but harming UX
- **Origin**: prompts.py:281 `"Include `# docs: <file_slug>` citations on their own lines."` (OUTLINE_PROMPT) and prompts.py:538 `"Include `# docs: <file_slug>` citations on their own lines for every non-trivial claim."` (SECTION_SYNTH_PROMPT) — the LLM is *instructed* to emit them.
- **Scrubber treatment**: helpers.py:723–760 (Pass 5 / OP-37) *splits stacked citations onto separate lines* but does **not** strip them.
- **Diagnosis**: this is intentional citation tracking. The reader perceives it as pollution because it interleaves with prose; the system perceives it as traceability metadata. Needs a UX decision: strip / footnote-collapse / preserve.

### 3.2 `<code-ref hash="…"/>` placeholders — **silent skip on vault miss**
- **Origin**: helpers.py:128–153 (sentinel emission) + helpers.py:854–924 (`_assemble_chapter_markdown`)
- **Bug site**: helpers.py:902–906
  ```python
  sentinel = hash_to_sentinel.get(ref)
  if not sentinel:
      continue  # audit already flagged invented refs
  ```
  The comment claims the audit caught invented refs upstream — but ch08 ships 88 unresolved tags, so the audit did *not* catch them. Either the audit threshold lets them through, or the LLM is emitting the `<code-ref hash="…"/>` literal *inside* `prose_md` (which the assembler doesn't process — it only resolves entries in `code_refs[]`).
- **Fix direction**: either substitute them in `prose_md` at assembly, or make audit hard-fail on any `<code-ref` substring found inside `prose_md`.

### 3.3 Orphan 12-char hex hashes in prose
- **Hypothesis**: the LLM emits the bare hash both in `code_refs[]` (the array — handled correctly) *and* on its own line inside `prose_md` (handled as raw text). The synthesizer's prompt explains both forms; the model is mixing them.
- **Fix direction**: regex Pass-10 in `_scrub_assembled_markdown` (helpers.py:846): `^\s*[0-9a-f]{12}\s*$` line-strip in prose regions only (skip inside fences). Trivially safe.

### 3.4 `# ...(truncated)` markers
- **Origin**: helpers.py:805–846 (Pass 8 / OP-70) — the scrubber *adds* this marker honestly when it detects a code block whose last line is partial.
- **Root cause is upstream**: the corpus chunker/ingestion truncates code blocks mid-command before they ever hit the vault. The marker is correct downstream signaling.
- **Fix direction**: trace ingestion's per-chunk char/token cap (likely in `apps/fastapi/services/discovery.py` or the corpus chunker), raise it so commands like `apt install ./docker-desktop-amd64.deb` aren't cut before their flags. Per `feedback_kd_quality_over_speed.md`: tokens are free — raise caps.

### 3.5 Duplicated H2 sections
- **Origin**: hierarchical_synth.py Phase D final-section assembly. Searches show **no heading-dedup step exists** — sections are merged in outline order without uniqueness check.
- **Why it manifests**: when Phase A.5 bucket-splits a large chapter (170+ hashes), the outline emits Parts 2–N for the same logical topic. ParetoBandit re-pins the same deployment, refiner produces near-identical prose, assembler glues them all in.
- **Fix direction**: in `hierarchical_synth.py` around line 917 (Phase D), dedup by `(heading + first 200 chars of prose)` hash — collapse collisions to one section or rename `(part 2)` deterministically.

### 3.6 Stub-placeholder challenges/flashcards
- **Symptom**: ch03 ships `{"front": "What is the core concept of DNS resolver issues?", "back": "Refer to the DNS resolver issues section in this chapter for the answer."}` — clearly a fallback template, not real content.
- **Origin**: hierarchical_synth.py outline generation. When Pydantic schema validation on the LLM's response fails or times out, *some* fallback emitted these placeholders.
- **Fix direction**: gate chapter emission on `len(challenges) ≥ 1KB AND chapter-specific terms detected`. Drop or re-run subtask otherwise.

### 3.7 Ch02 mis-routed content
- **Symptom**: planner assigned 71 files to ch02 titled "Docker Account Management" but the file list (from plan.json) is dominated by `0228-docs-docker-com-desktop-setup-install-linux-debian-md`, `0229-...-fedora-md`, `0232-...-mac-install-md`, etc. → planner *itself* placed Linux/Mac install docs in the Account chapter.
- **Origin**: planner prompt + the embedding-clustering that drives file→chapter assignment (referenced in `project_planner_map_replacement.md`)
- **Fix direction**: deeper investigation. Either the planner prompt isn't enforcing title-coherence, or the MAP step's chapter centroids are off for Docker corpus. Lower-priority than the cosmetic fixes; needs its own iteration.

### 3.8 Three missing chapters (06, 09, 10)
- **Symptom**: planner enumerated 10 chapters; only 7 in MinIO. Ch06 has 138 files (largest after ch08); ch09 has 105; ch10 has 59. Ch08 mtime 23:58:22 is the last write before Celery SIGTERM.
- **Origin**: `apps/fastapi/celery_app.py` keeps `task_soft_time_limit=7080` (2h) as deliberate forcing function (see `docs/KD-CANARY-V7-V10-FINDINGS-2026-05-14.md` Batch 1 note).
- **Fix direction**: write a stub README for any planned chapter that lacks a final synthesis result so chapter visibility doesn't silently lose data. Separately, decide whether 2h ceiling stays or rises.

---

## 4. Prioritized fix plan (quality-first)

Per memory `feedback_kd_quality_over_speed.md`: tokens are free, runtime isn't the binding constraint — raise caps and add iterations before reaching for smaller/faster models. The order below maximizes content quality at no speed cost until step E.

| # | Lever | Code site | Effort | Expected gain | Risk |
|---|---|---|---:|---|---|
| **A** | Orphan-hex Pass-10 in scrubber: `^\s*[0-9a-f]{12}\s*$` line-strip in prose regions | `helpers.py:_scrub_assembled_markdown` after line 846 | 20 min | -267 noise lines | nil |
| **B** | Hard-fail audit on any `<code-ref` substring inside `prose_md` (don't silently skip at assembly) | `helpers.py:902–906` + audit fn | 30 min | -88 ch08 unresolved tags become refine signals | low |
| **C** | De-dup H2 within a chapter at Phase D assembly: `(heading + prose[:200])` hash collision → merge or `(part N)` rename | `hierarchical_synth.py` ~line 917 | 1 h | Ch03/05/08 stop emitting same section 6–7× | low — preserves bandit semantics |
| **G** | Gate challenges/flashcards on minimum length + chapter-keyword presence; re-run subtask or drop on stub detection | `hierarchical_synth.py` outline stage | 1 h | Ch03 placeholders never reach S3 | low |
| **D** | Raise per-chunk char/token cap in corpus ingestion so code blocks aren't truncated mid-command | `apps/fastapi/services/discovery.py` (or ingestion chunker — to be located precisely) | 1–2 h | 274 `(truncated)` → near-0 | medium — bigger chunks = larger prompts |
| **H** | UX call on `# docs:` inline citations: strip from final prose / collapse to chapter-end footnote / preserve as-is. Currently emitted by design at `prompts.py:281, 538` | `prompts.py:281, 538` + optional Pass-11 collapse | 1–2 h | -630 inline citation noise OR retained as footnotes | requires user decision |
| **E** | Once A–D ship: raise `task_soft_time_limit` 7080 → 14400 (4 h) so quality-driven Self-Refine iters can converge instead of axing at 2 h | `apps/fastapi/celery_app.py` + possibly `_THIN_SECTIONS_ACCEPT_LIMIT` in `distiller.py` | 5 min | Ch06/09/10 finish; trades wall-clock for completeness | medium — only after A–D so we aren't paying for more iters of polluted output |
| **F** | Re-investigate planner ch02 mis-route: inspect file→chapter assignment for title-coherence; either fix planner prompt or post-validate chapter contents against title | `apps/fastapi/graphs/knowledge/distiller.py` planner stage + see `project_planner_map_replacement.md` | 2 h+ | One mis-labeled chapter recovered; planner gets more robust | medium — deepest change |

**Recommended ship order:** A → B → C → G → D → H (decision) → E → F.

A/B/C/G are pure quality wins, no speed penalty. D removes the most visible runnability defect. H needs a UX call (citation tracking vs reading flow). E is the "tokens are free" lever — only after A–D so retries pay for clean output, not more pollution. F is the deepest planner work and can wait.

---

## 5. Quality preservation principles (carried forward)

From `docs/KD-SPEED-OPTIMIZATION-PLAN-2026-05-14.md`:

- Self-Refine still converges
- Audit gate still detects defects
- Bandit observations still accumulate
- Acceptance threshold stays at 0.85
- No early-termination of refine on iter 0 thin

Add to these:

- **Heading dedup is structural, not semantic** — collapsing 6× "repository:shortid" sections to 1 must not lose unique content. If two near-duplicate sections have non-overlapping `code_refs`, merge their `code_refs[]` into the survivor.
- **`<code-ref/>` leakage is a hard failure**, not a cosmetic one — audit should flag any unresolved sentinel in `prose_md` at the same severity as a missing-hash regression.
- **Stub-placeholder content is worse than missing content** — emitting "Refer to section X for the answer" actively misleads the reader. Better to ship `challenges.md` absent and visibly trigger "no challenges available" than to ship a placeholder that looks real.

---

## 6. Next-session pickup

### Minimum-viable handoff (2 h)
- A + C + G: drop the most-visible cosmetic pollution and the placeholder-stub problem in one batch. ~2 h total. No risk to architecture.

### Single biggest correctness fix (30 min)
- **B**: hard-fail on unresolved `<code-ref/>` in prose_md — this turns 88 silent leaks per chapter into refine signals, which the existing Self-Refine loop can act on.

### Decision needed from user before shipping (H)
- Inline `# docs:` lines: **strip / footnote / keep**. Default behavior is "keep inline" per current prompt at `prompts.py:281,538`. The audit shows 630 of these in 5 chapters — they dominate the noise floor.

### Open from this audit
- Ch02 planner mis-routing (item F) — needs its own session
- Whether the 2h Celery `task_soft_time_limit` should rise to 4 h (item E) — depends on speed batches in `docs/KD-SPEED-OPTIMIZATION-PLAN-2026-05-14.md` actually closing the wall-clock gap after A–D ship

---

## Cross-references

- `docs/KD-CANARY-V7-V10-FINDINGS-2026-05-14.md` — architecture validation log; this doc's "missing chapters" + "Docker content quality (20–30% missing hashes)" are listed there as Open Issue #1
- `docs/KD-SPEED-OPTIMIZATION-PLAN-2026-05-14.md` — Batches 1–4 already shipped (validated in v8–v10); item E here is the deliberate next step after those
- `docs/KD-SESSION-2026-05-14-FINDINGS.md` — earlier canary v1–v6 evidence
- `apps/fastapi/graphs/knowledge/helpers.py` — `_scrub_assembled_markdown` (line 626), `_assemble_chapter_markdown` (line 854), bug site at line 902–906
- `apps/fastapi/graphs/knowledge/hierarchical_synth.py` — Phase D assembly around line 917 (no heading dedup)
- `apps/fastapi/schemas/knowledge/prompts.py:281, 538` — `# docs: <file_slug>` emission instructions
- Local audit artifacts: `/home/rafaelcoelho/minio-data/kd-audit/` (10 files downloaded via aistor MCP for evidence-quoting)

---

## Evidence appendix — direct quotes from the audited artifacts

**Ch01 opener (line 3) — chapter starts mid-tutorial:**
```
## Step 4: Build the Next.js application image — Part 1 of 3
```

**Ch01 lines 7–8 — citation + orphan hash:**
```
... This step is critical for environments where Docker Desktop isn't pre-installed, as it handles all necessary configurations for the Docker engine to function correctly. # docs: 0231-docs-docker-com-desktop-setup-install-linux-ubuntu-md
85f67f4492f8
```

**Ch01 line 30 — truncated shell command:**
```
$ sudo apt install ./docker-desktop-amd64.deb # ...(truncated)
```

**Ch08 line 266 — unresolved code-ref placeholder:**
```
<code-ref hash="6d5b96e2bad4"/>
```

**Ch03 challenges.md — entire file (273 B):**
```
1. Explain the key concept covered in this chapter.
2. Provide a working code example demonstrating its primary use.
```

**Ch03 flashcards.json — representative card:**
```json
{"front": "What is the core concept of DNS resolver issues?", "back": "Refer to the DNS resolver issues section in this chapter for the answer."}
```

**Ch02 (mis-routed) — first H2 is sensible:**
```
## Sign in to Docker Desktop
```
…but the next sections are `## Install on Debian`, `## Install on Fedora`, `## Install on RHEL`, none of which belong under "Docker Account Management".
