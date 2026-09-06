# Synth — known issues from the claude-code study run (2026-09-05)

Found by direct log/artifact inspection of all 11 chapters of the
`claude-code` study run
(`docs-distiller/study/claude-code/59353d70-e1ef-4d06-9708-947b1273017f`),
now complete.

## ✅ Status: all 9 issues addressed (2026-09-05) — 3 confirmed live in production the same day

8 of 9 got real code fixes, applied and syntax-verified across 12 files
in `apps/fastapi/domains/dd/synth/`. A fresh `claude-code` study run
(post-redeploy) directly confirmed 3 of them working as designed on
chapter 1:

- **#7** — `[sawc_write] ... 1 fallbacks (failed = {'APITimeoutError': 2})`
  now appears in the log (never visible before this fix); confirms the
  chapter's section failures are plain timeouts, not context-overflow or
  schema issues.
- **#8** — `[render_audit_write] sawc source = best-seen, audit_passed = False...`.
  Verified against content: iteration 1 scored best (64%) and had 1/2
  real sections; iterations 2-5 all regressed (50%→43%→50%→29%,
  ending 0/2 empty). The shipped README is iteration 1's content, not
  iteration 5's — before this fix, it would always have been iteration 5's
  (the worst one).
- **#1** — `0 citation-fallback (chapter-wide)` across all 5 iterations,
  and the rendered section's citations are genuinely section-scoped (3
  sources, not the chapter-wide pool). Every prior run this whole session
  showed 100% citation-fallback, every section, every time.

The chapter still ended `needs_review` — content quality is still capped
by a live, sustained Rotator-side timeout pattern that's outside Synth's
control — but the *system's behavior around that* is now verifiably
correct: honest status reporting, best-draft preservation, and correct
per-section routing, instead of silently shipping the worst draft under a
"done" label with no diagnostic trail.

| # | Fix | Files |
|---|---|---|
| **8** | mgsr's own comment claimed it tracked best-seen; it never did. `sawc_write` now reconstructs the just-finished iteration's versioned key and promotes it to `best_seen_sawc_path`/`best_seen_score` only if its score beats the running record. `render_audit_write` now reads `best_seen_sawc_path` first, falling back to the latest pointer only if unset. | `sawc/service.py`, `render/service.py` |
| **7** | `_draft_one_section`'s failure paths now return an `error_reason` (5th tuple element) and log at `WARNING`/`INFO`; total section failures aggregate into a per-chapter `error_breakdown` in the summary log, mirroring `digest_construct`'s existing pattern. | `sawc/service.py` |
| **1** | Root cause found: `SectionContribution` had no `source_key` field at all — `sawc_write`'s routing read a field that never existed, 100% of the time, regardless of digest's success rate. Added the field (backfilled programmatically after LLM parsing, never asked of the model) and bumped the schema version. | `digest/schemas.py`, `digest/service.py` |
| **3** | `"file"`/`"path"` weren't in the misroute detector's `NOISE_IDENTS` — too generic to signal real relevance, which is exactly why `rm/mv/cp` under a "Checkpoint State Recovery" subtopic slipped through (shared only the word "file"). Added to the noise set. | `render/params.py` |
| **2** | Added a `_normalize_outline_dict` pre-validation pass — remaps descriptive-slug `section_id`s to `s<N>` (and fixes up `prerequisites` refs accordingly) and truncates over-long headings to their leading clause, *before* Pydantic ever sees them, instead of reject-and-hope-the-repair-call-fixes-it. Also strengthened both the draft and repair prompts with explicit WRONG/RIGHT examples for both fields. | `outline/service.py` |
| **5** | Prose-mode writer prompt now explicitly demands ≥1 concrete artifact (path/command/config key) per subtopic and varied openings across subtopics — the exact gap behind chapter 3's repetitive, ungrounded filler. | `sawc/service.py` |
| **6** | Added a render-time regex normalization (`_fix_stray_slash_command_space`) that fixes `"/ command"` → `"/command"` on the first line of a fenced code block — scoped tight (leading-slash-plus-whitespace only) so real code/division is untouched. Fixes the bug regardless of whether it originated in LLM generation or the ingested source itself. | `render/domain.py` |
| **9** | Scoped version (not the full "v2 loop" — see below): closed the actual self-refine loop. `checklist_stats` now carries the real `failed_feedback` strings (previously only a count was passed through state); `sawc_write` reads them on a RETHINK iteration and threads them into the writer prompt as "PRIOR ATTEMPT FEEDBACK — FIX THESE," so a rewrite has real signal instead of rerunning blind. | `checklist/service.py`, `sawc/service.py` |
| **4** | Investigated, no code fix applied. No new evidence pointed to a specific, fixable root cause beyond what's already mitigated (second-wave retry) — the doc's own framing ("may be a non-issue once provider capacity recovers") held up. Left as a watch item, not a speculative change. |  |

**Final study result:** `5/11 completed, 0 failed, 6 needs_review,
final_status = "needs_review"`. Done: chapters 1, 2, 4, 10, 11.
Needs-review: chapters 3, 5, 6, 7, 8, 9 — every one of them due to
`sawc_write` failing completely on at least one section despite the
underlying source material generally being adequate (see issue #7). Not a
single chapter failed for lack of source coverage or a genuine,
un-recoverable content-quality problem — chapters 4 and 11 both proved a
healthy RETHINK loop reliably fixes real quality issues (50%→86% and
64%→100% respectively). `book_harmonize` correctly ran against only the
5 `done` chapters (confirms this session's exclusion fix worked
end-to-end), though it extracted zero atomic claims across all 5 —
plausibly one more sustained-outage casualty, unconfirmed.

**Chapter 10: no new Synth issues — a clean confirmation run** (14/14
checklist, single iteration, audit passed). Worth noting separately
(out of this doc's scope, it's a Rotator/Groq config matter, not a Synth
code issue): `outline_sdp`'s repair attempts surfaced a raw
`"model \`qwen/qwen3.8-27b\`/\`qwen/qwen3.6-27b\` is blocked at the
project level"` Groq error, the same class of issue from earlier in this
conversation — now hitting two distinct qwen variants, after 40 retries
each. Worth checking the Groq console directly.

**Chapter 9 disambiguates two things:** issue #7 (writer failure) is
confirmed independent of issue #1 (citation routing) — this chapter had
working routing (0 fallback triggers, both sections got primary sources)
yet the writer still failed completely both iterations. And chapter 8's
size-correlation hypothesis for #7 is weakened, not confirmed — chapter 8
failed totally with the *largest* vault this session (313 entries),
chapter 9 failed totally with a *small* one (43 entries) — total writer
failure isn't exclusive to large chapters, which points back toward a
general sustained-outage explanation. Both readings stay open until #7's
logging fix actually reveals the error type.

**⚠️ Issue #8 (below) is the most severe finding in this document — read
it first.** It affects every chapter that regresses across RETHINK
iterations, which per this run's own logs is common. (Chapter 7's
iterations genuinely improved 1/3 → 2/3 sections, so "always render
latest" happened to coincide with "best" there — a useful reminder that
issue #8 doesn't cause visible harm on every chapter, only when a later
iteration regresses, as chapters 3 and 6 did.)

**Issues #1 and #7 reconfirmed in chapter 7** — every section again shows
`citation-fallback (chapter-wide)`, and the one section that failed
completely (0 subtopics) again has no error-reason in the logs, same as
chapter 5.

**Confirmed working in production during chapter 5's run** (this session's
earlier fixes, not a new issue): the `infra_degraded` discrimination logic
correctly fired in *both* directions now observed — chapter 3 gave a
genuinely infra-degraded low score one extra RETHINK attempt; chapter 5's
low score (`36%`, genuine judge review, no call failures) correctly
triggered `HALT no-recovery` immediately instead of wasting an iteration.
That's the intended behavior in both cases.

**Confirmed working in production during chapter 4's run** (this session's
earlier fixes, not a new issue): the CoRefine RETHINK loop delivered a
genuine content-quality recovery, not just an infra-driven retry — iter 1
scored 50% (1/2 sections written), iter 2 scored 86% (2/2 sections,
cleanly passing). Both iterations show real `pre`/`llm` structural review
with no call failures, confirming the loop can and does improve real
output quality when the underlying issue is actually fixable content, not
just an outage.

**Confirmed working in production during chapter 3's run** (this session's
earlier fixes, not new issues — recorded here for completeness): the
sustained-outage halt fired correctly — `"HALT sustained-outage (2
consecutive infra-degraded iterations >= 2); best-seen-rescue applies"` —
stopping after 2 RETHINK iterations instead of grinding to
`MAX_REFINE_ITER=5`. The fail-closed gate also correctly reported
`needs_review (3/11)` instead of `done` for the resulting broken chapter.

---

## 1. digest_construct's per-section citation routing has never once worked

**Severity: highest — 100% reproducible, independent of provider health.**

Every section, in every chapter, in every run observed this session —
including chapter 1's clean 8/10 pass with zero infra failures — ends up
logging:

```
[sawc_write] <section>: digest routed 0 sources to this section; falling back to chapter-wide (N sources) for citations
```

This is not a symptom of digest's failure rate (the second-wave retry
already added this session addresses that). Sections whose sources *did*
digest successfully still get zero section-specific routing. The routing
logic itself — matching `contributes_to.section_id` from
`digest/schemas.py`'s `SectionContribution` back to a section's citation
pool in `sawc/service.py`'s `_run_section` — has a bug independent of call
success rate.

**Now confirmed across 4 consecutive chapters, every section, regardless
of outcome quality** — including chapter 4's clean 86%-passing, 2/2-section
result. This is the single most reproducible issue in this document.

**Where to look:** `digest_construct`'s `SourceDigest.contributes_to`
population (`digest/service.py::_digest_one_source`,
`digest/domain.py::build_per_section_index`) vs. how `sawc/service.py`
reads `per_section_index.get(sid)` — the two sides may be keying on
different identifiers, or the LLM-side `contributes_to.section_id` values
may not be validated/coerced to match `valid_section_ids` before indexing.

---

## 2. outline_sdp's initial 3-sample draft fails to parse 100% of the time (this chapter)

**Severity: high — 100% reproducible for `ch-01-installation-and-authentication`
across every run this session.**

Every single run logs:

```
[outline_sdp] ...: ALL 3 samples failed to parse; emitting heuristic fallback outline
```

...followed by a repair loop (often itself timing out) and a programmatic
HARD-TRIM. The fallback path works, so output ships — but a 100%
reproduction rate at the *first* stage of every run for this specific
chapter is not random noise.

**Root cause now has real evidence, from chapter 3's run** — the pydantic
rejection was captured in full for the first time, and it's specific and
actionable:

```
pydantic-reject — sections.0.section_id: Value error, section_id 'config-hierarchy-scopes'
must match /^s\d+$/ (e.g. 's1', 's12'); sections.1.section_id: Value error,
section_id 'file-level-customization' must match /^s\d+$/ ...
```

The model is consistently emitting **descriptive slugs** as `section_id`
(`config-hierarchy-scopes`, `file-level-customization`) instead of the
required `s1`/`s2`/`s3` format the schema demands. This reads like the
model finds a human-readable slug more natural to generate than an
arbitrary `s<N>` token, and the prompt isn't constraining it strongly
enough (few-shot examples using literal `s1`/`s2` might not be salient
enough, or the field description alone isn't sufficient). This is now a
scoped, fixable prompt-engineering issue, not an unknown — check
`outline/prompts.py`'s section_id instructions and consider adding an
explicit "NOT a descriptive name" negative example, or relaxing the
schema to accept a slug and normalize it to `s<N>` programmatically instead
of rejecting outright.

**A third, distinct validation failure mode, from chapter 9** — this time
not a parse failure or section_id format issue, but a heading word-count
violation, rejected twice in a row:

```
sections.0.heading: Value error, heading must be 2-8 words; got 11
('Extending Claude Code with Skills, Hooks, System Prompts, and Project Context')
```

The model keeps trying to cram a full comma-separated topic list into one
heading instead of a short 2-8 word title. Same underlying pattern as the
section_id issue — the model reaches for the most natural/descriptive
phrasing, and the schema's constraint isn't salient enough in the prompt
to override that instinct. Three distinct schema fields now confirmed
hit by this same class of problem (`section_id` format, heading length,
plus whatever caused the pure parse failures in other chapters) suggests
the fix should be structural — e.g. a shared "constraint-compliance"
few-shot block reused across all of outline's field validators — rather
than patching each field's instructions separately.

---

## 3. Code-misroute detector has a confirmed false negative

**Severity: medium — concrete content-quality defect, single confirmed instance.**

Chapter 2's rendered README shipped this under "Checkpoint State Recovery"
(prose about `/rewind` restoring file/conversation state):

```bash
rm file.txt
mv old.txt new.txt
cp source.txt dest.txt
```

— a code block with no relation to the subtopic. In the **same chapter**,
a different mismatch was correctly caught and self-omitted:

```
> _(Code example omitted — it did not match this subtopic and was likely misrouted.)_
```

So the detector works but isn't catching every case. Likely only checks an
explicit signal (hash/id mismatch) rather than semantic relevance between
the subtopic's prose and the chosen code body. Worth checking
`render`'s dedup/misroute logic (the "1 misrouted block(s) omitted" /
"1 recycled code block(s) cross-referenced" log line's source) against
what specifically it validates.

---

## 4. digest_construct's raw failure rate is persistently high even outside the confirmed outage window

**Severity: medium — likely mostly provider-side, but unverified.**

Observed failure rates this session: 53%, 73%, 80% (best), 47% (85% at one
point during the confirmed sustained Rotator outage), and 75% in chapter 2
(worst, unrelated to that outage window). Some of this is genuinely
provider-side rate-limiting (out of Synth's control — see the Rotator's
own arm-pool health, tracked separately). But it's worth checking whether
`digest_construct`'s own per-call payload size (`_MAX_SOURCE_CHARS =
100_000`) or timeout budget is disproportionately unfavorable compared to
`sawc_write`/`checklist_eval`, which recovered better under the same
conditions in the same runs. If digest's calls are simply larger/slower
per-request than other nodes', that alone would explain a higher timeout
rate under identical arm-pool stress.

---

## 5. Prose-mode (no-code) sections produce vague, repetitive filler text

**Severity: medium — concrete content-quality defect, confirmed in chapter 3.**

When a section has no routable code hashes, `sawc_write` falls to
`prose_mode` (see `n_routed_hashes == 0` gate in `sawc/service.py`). The
resulting prose in chapter 3's "Explore the directory" section is thin and
repetitive — all 3 subtopics open with near-identical phrasing
("Exploring the directory reveals...", "Exploring the directory
clarifies...", "Exploring the directory helps locate...") and contain zero
concrete artifacts (no directory listing, no example config file, no
command) despite the topic being inherently structural — a `tree
~/.claude` or a sample `settings.json` would ground this immediately.

This is distinct from issue #1 (routing) — this section's citations were
present and correct, it just had no code to show. The gap is in the
prose-mode writer prompt itself not pushing hard enough for concrete,
non-generic detail when there's no code block to anchor around. Worth
checking `sawc/prompts.py`'s prose-mode branch for whether it asks for
specifics (file paths, example structures) or just asks for an
"explanation," which invites this kind of filler.

---

## 6. Stray-space slash command syntax in generated inline commands

**Severity: low — single confirmed instance, but a real functional accuracy defect.**

Chapter 4's "Add Marketplace via HTTPS URL" subtopic renders:

```
/ plugin marketplace add https://github.example.com/platform/claude-plugins.git
```

`/ plugin` (with a space) is not a valid Claude Code slash command — it
should be `/plugin` with no space. A reader who copy-pastes this verbatim
gets a failure. Every other inline command in this chapter (and the prior
three) is clean, so this reads as an isolated writer-generation slip
rather than a systemic normalize-pass bug — but nothing in the pipeline
(writer validation, render's normalize pass, the checklist judge) caught
it. Worth a light-touch fix: a post-generation regex check for `` `/ ``
followed by a known command word inside inline/fenced code, since slash
commands are a well-known, enumerable vocabulary for this framework.

---

## 7. sawc_write's total per-section draft failure is invisible in logs

**Severity: medium-high — a diagnosability gap, now confirmed in chapters 5 and 8
(the latter a total-failure case that raises a real size-correlation hypothesis).**

**Chapter 8 update:** the worst instance yet — `0/3 sections written` in
370 seconds (the longest `sawc_write` run this session) on the chapter
with the *most* source material observed (19 sources, 313 vault entries
pre-fold, 134 post-fold). Richest inputs, worst outcome, longest runtime
before giving up. That combination suggests a real hypothesis distinct
from generic Rotator flakiness: **a context-overflow failure specific to
large chapters** — `sawc_write` does have a context-overflow retry
(halved vault budget) added earlier this session, but if the *initial*
prompt for a heavily-sourced section is large enough, the halved retry
might still not fit. This can't be confirmed without the logging fix
below — it's exactly the kind of thing `error_breakdown` would surface
(a spike in `context_overflow`-tagged failures correlating with
`n_sources`/vault size).

Chapter 5's one surviving section (`s1`, 12 vault entries across 2
sources — not source-starved) failed **all** of its draft attempts and
fell to a placeholder:

```
[sawc_write] claude-code/ch-05-cloud-and-enterprise-deployment: 0/1 sections written, 1 fallbacks, ...
[render_audit_write] ...: 1/1 section(s) are EMPTY placeholders (writer produced 0 subtopics) — failing audit.
```

Neither line says *why* the drafts failed. Checked `_draft_one_section`
directly (`sawc/service.py`): its call-failure and parse-failure paths
only call `emit_progress` (an internal SSE event), never
`logger.warning` — so nothing lands in celery pod logs, unlike
`digest_construct`'s per-source failures, which got an `error_breakdown`
dict + `logger.warning` lines earlier this session specifically to make
this diagnosable. `sawc_write` never got the equivalent treatment. When a
section fails completely despite having real source material, there's
currently no way to tell from logs alone whether it was a call timeout, a
parse failure, or a schema rejection.

**Fix:** mirror `digest_construct`'s pattern — add `logger.warning` at
`_draft_one_section`'s failure points and surface an aggregate
`error_breakdown` per chapter in `sawc_write`'s summary log line, the same
way `digest_construct` already does.

---

## 8. "Best-seen-rescue" never actually rescues anything — confirmed dead machinery

**Severity: CRITICAL — silently defeats the safety net for every chapter that regresses across RETHINK iterations.**

`graph.py` logs `"best-seen-rescue applies"` on every no-recovery/budget/
sustained-outage halt, implying render falls back to whichever iteration
scored highest. **It doesn't.** Confirmed by direct code inspection of all
three nodes involved:

- `render_audit_write_run` (`render/service.py`) computes its sawc input
  as `sawc_key = sawc_latest_key(slug, chapter_id)` — always the **most
  recently written** sawc blob. It never references
  `state.get("best_seen_sawc_path")` anywhere in the file (checked via
  grep — zero occurrences).
- `sawc/service.py`'s own comment says *"best-seen iteration tracking —
  checklist score updated in mgsr_replan after sawc returns"* — but
  `mgsr/service.py` has **zero references** to `best_seen_sawc_path` or
  `best_seen_score` anywhere. The node that's supposed to do the score
  comparison and update the pointer never does.
- `sawc_write` itself only ever *forwards* `best_seen_score` when
  `incoming_best_score is not None` — but nothing ever sets it to a real
  value in the first place (iteration 1 has no incoming score to
  forward), so it stays `None` for the entire graph run, on every
  chapter, every time. Same dead-field pattern as the
  `prev_checklist_score` bug fixed earlier this session — except this one
  is still live.

**Confirmed live impact, chapter 6:** iteration 1 wrote 1/2 sections
successfully; iteration 2 regressed to 0/2 (worse, under the same
sustained-outage conditions). The halt fired
`"HALT sustained-outage... best-seen-rescue applies"`, but the final
shipped chapter has **both** sections empty — iteration 2's worse result,
not iteration 1's better one. This is not a one-off: any chapter whose
RETHINK loop makes things worse (a real, observed pattern this session —
chapter 3 went 43%→36%, chapter 6 went 43%→29%) silently ships its worst
attempt instead of its best, with logs actively claiming the opposite
happened.

**Fix:** requires wiring across three nodes:
1. `mgsr_replan` (or `checklist_eval`, which already has the fresh score)
   must actually compare the current iteration's score against
   `state.get("best_seen_score")` and update both
   `best_seen_sawc_path`/`best_seen_score` in its state patch when the
   current iteration is better.
2. `render_audit_write_run` must read `state.get("best_seen_sawc_path")`
   (falling back to `sawc_latest_key` only when it's unset) instead of
   unconditionally reading the latest pointer.
3. Verify with a synthetic case: force iteration 2 to score worse than
   iteration 1, confirm the rendered chapter reflects iteration 1's
   content.

---

## 9. mgsr_replan's corrective actions are computed but never applied

**Severity: medium — a known, longstanding design gap (not new this session), reconfirmed concretely in chapter 8.**

Chapter 8: `mgsr_replan` computed 4 real corrective actions at `conf=0.92`
(high confidence) — but because the chapter hit `HALT no-recovery`
immediately (genuinely low score, no RETHINK attempt), those actions were
discarded entirely. This isn't new — mgsr's own schema documents this
directly (`HaltReason` literals tagged `"(v2 only)"`, `iteration: int = 0
# v1 always 0; v2 loop bumps`, fallback literal `"v1_no_loop"`) — the
graph's real CoRefine loop only ever uses checklist's raw `pass_rate` to
decide whether to loop, never mgsr's own analysis of *what specifically*
to fix. mgsr is currently pure audit telemetry, not an active participant
in repair. Chapter 8 is a concrete example of the cost: 4 well-reasoned,
high-confidence actions (likely "retarget this section," "merge into
adjacent," matching the placeholder text's own suggestion) that could
plausibly have fixed the chapter, computed and thrown away in the same
breath.

**Fix:** this is the "v2 loop" already scoped in mgsr's own schema
comments — wire `_route_after_mgsr` to route mgsr's `actions` back into
`sawc_write`'s next iteration (targeted re-draft of just the flagged
sections) instead of a blind full re-run, and bump `iteration` per the
v2 semantics already reserved for this. Larger scope than the other
issues here — treat as a follow-up project, not a quick fix.

---

## Fix order recommendation (once the study run completes)

1. **#8** (best-seen-rescue is dead) — do this first, above everything
   else. It's the highest-severity, highest-blast-radius finding: it
   silently ships the *worst* iteration instead of the best one on every
   chapter that regresses, and the logs actively claim otherwise, which
   would mislead any future debugging that trusts them.
2. **#7** (sawc_write failure observability) — raised above #1 after
   chapter 8's total-failure (0/3 sections, richest chapter this session).
   Cheap, mechanical port of an already-proven `digest_construct` pattern,
   and now the fastest path to confirming or ruling out the size/
   context-overflow hypothesis before investing in anything downstream of it.
3. **#1** (citation routing) — purely a code bug, no dependency on
   provider health, confirmed across every chapter regardless of outcome
   quality.
4. **#3** (misroute false negative) — concrete, scoped, low risk.
5. **#2** (outline parse failure) — now has a specific, actionable lead
   (descriptive slugs instead of `s<N>` format) — a prompt/schema fix, not
   a fresh repro investigation.
6. **#5** (prose-mode filler quality) — scoped to one prompt branch, worth
   pairing with #1 since fixing routing may reduce how often prose_mode
   triggers at all.
7. **#6** (stray-space slash commands) — cheap, low-risk regex check;
   bundle with whichever writer-prompt work touches #5.
8. **#4** (digest failure rate) — may turn out to be a non-issue once
   provider-side capacity recovers, but check the payload-size hypothesis
   regardless — likely shares a root cause with #7's new hypothesis.
9. **#9** (mgsr actions unused) — largest scope of anything here, a real
   "v2 loop" project rather than a fix; do last, after everything above
   has stabilized the pipeline enough to make wiring a new active-repair
   path safe to build on.
