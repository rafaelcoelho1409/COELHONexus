# LangFuse SOTA + rollout plan — 2026-09-22 (reorg landed same day, §§2/4/5 current)

Scope: `apps/server/` — `infra/langfuse/`, `infra/opentelemetry/` (export path),
`api/v1/` + `domains/{ycs,settings}` (consumers). SDK: `langfuse>=3,<4`
(OTel-native v3). SOTA sourced directly from current Langfuse docs (v3/v4
SDK, prompt-management-at-scale, evaluation core concepts).

## 1. Architecture (as built)

Two planes, one rule (**fail-soft, never raise**):

- **Trace plane (OTel):** spans → `infra/langfuse/service.py:add_exporter`
  (attached by `infra.opentelemetry.service.init_otel`) → HTTP OTLP to LangFuse v3
  `/api/public/otel`. `coelho.langfuse.keep` gates export
  (`infra/langfuse/domain.py:should_keep_span`). No SDK involved.
- **SDK plane (`infra/langfuse/`):** everything OTel can't do — sessions,
  scores, prompts, evals. Final tree (§2-conformant, verified 2026-09-22):
  `service.py` (I/O shell: client, session, record_score, flag_for_review,
  build_langchain_callback) · `spans.py` (current-span setters, §4-blessed)
  · `domain.py` (pure encoders) · `keys.py` (`langfuse.*` namespace) ·
  `params.py` · `patterns.py` · `prompts.py` (managed-prompt fetch) ·
  `evals/{datasets/{uploader,runner},judges/{service,domain,params,patterns,prompts}}`.
- Env: `LANGFUSE_HOST` (or derived from `LANGFUSE_OTLP_ENDPOINT`),
  `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`. Helm: `k8s/helm/values.yaml`.
- `settings/runtime/observability/spans.py` wraps the ONLY external LLM
  connection with `gen_ai.*` spans (model + usage tokens → cost tracking).

## 2. Package inventory (post-reorg)

| Module | Role | Status |
|---|---|---|
| `service.py` | I/O shell: client, sessions, scores, review flags, callbacks | ok |
| `spans.py` | Current-span setters (kept — dissolving would contaminate `domain` or bloat `service`) | ok |
| `keys.py` / `params.py` / `patterns.py` / `domain.py` | Namespace / tunables / regex / pure encoders | ok |
| `prompts.py` | Managed fetch, 60s TTL + `_MISS`, local fallback, override decorator | ok |
| `evals/datasets/` | `uploader` (CLI) + `runner` (harness); fixtures `k8s/helm/files/langfuse/ycs` → `/etc/langfuse-fixtures/` mounts | ok, offline-only |
| `evals/judges/` | `service` (incl. 3 LLM judges + choke point) · `domain` (renders, parser, novelty) · `params` · `patterns` · `prompts` (rubric catalog) | ok except §4.1 (single line) |

## 3. Consumer map (complete 2026-09-22)

- `api/v1/`: ONLY `ycs/agents/router.py` — 4 `session("ycs")` roots
  (`ycs.ask.run`, `ycs.ask.stream.run`, 2 nested) + full I/O/meta stamping
  via `infra.langfuse.service.*` + `infra.langfuse.spans.*`.
- YCS: extract ×3 roots, neo4j/qdrant ingest roots, embedding-migration
  root; pipeline dispatch + transcript spans keep-flagged; RAG node
  decorators + shared I/O encoders (`runtime/observability/`).
- Settings: chat/embedding `gen_ai.*` spans — inherited by every feature.
- NOT wired: `build_langchain_callback`, datasets uploader/runner, judges,
  `prompts.invalidate_cache` (no runtime callers).

## 4. Known defects (fix before scaling usage)

1. `evals/judges/service.py::run_rubric_judge` does lazy `import domains`
   (single line — the S8 injection point). infra→domains inversion.
2. WITHDRAWN 2026-09-23: the reported import cycle was stale bytecode, not a live
   cycle — traceback line numbers referenced a previous `service.py`
   (with a `metrics` import that no longer exists); after clearing
   `__pycache__`, bare `import domains` succeeds. No code change needed.
   Lesson recorded: clear `__pycache__` before diagnosing import cycles
   (mixed cpython-313/314 caches were present).
3. (fixed 2026-09-22: shell consolidation, `keys.py`, judges dissolution,
   `evals/datasets` nesting, lying docstrings, `rubric.md` pointer.)

## 5. Ship log (S0–S8 shipped 2026-09-22/23; S9 is process)

- **S1** ✅ producer-side `CeleryInstrumentor` in `init_otel` (worker side
  already existed) → single trace api→worker. Confirm in UI with one run.
- **S3** ✅ `langfuse.observation.type=generation` on chat spans; tags ride
  existing `langfuse.tags` baggage via `session(extra)` — no new code.
- **S5** ✅ YCS: `rag.service.resolve_prompt` (system-message override) + 12
  nodes + grader. `query/service.py` streaming builder = follow-up.
- **S7** ✅ `ycs.grader.keep_rate`,
  POST `/feedback` + `AskFeedbackRequest` + `trace_id` in `/search` response.
- **S8** ✅ `chat_fn` injection (judges score without `domains`);
  the withdrawn import-cycle report was stale bytecode; uploader/runner wiring verified
  write-free. REAL UPLOAD left for live env (re-runs duplicate items —
  no source IDs; dedupe strategy before scheduling).
- **S9** ▶ runbook below (process, not code — all code prerequisites landed
  in S8).

### S9 runbook

```bash
# inside the server image (fixtures mounted at /etc/langfuse-fixtures):
python -m infra.langfuse.evals.datasets.uploader \
  /etc/langfuse-fixtures/ycs/qa_pairs ycs.qa_pairs.v1
```
Then run the YCS Ask nightly dataset evaluation with `ragas_relevance`
(inject `chat_fn` — no `domains` import needed).
Promote prompts by moving the `production` label only after the
experiment beats baseline; configure online eval rules + score/cost
alerts in the LangFuse UI (no code — UI objects).

## 6. Code standard binding (CODE-CONVENTIONS.md + session rules)

§2 triggers (no placeholders — `metrics.py`, per-judge dirs, per-tool
`service.py` all correctly refused); §8 dotted unaliased refs +
sibling-first; §8-Exc-1 (task.py out of eager chains, lazy Celery imports);
fail-soft on every new call; thin `task.py`/`node.py` bridges;
`domains/`↛`api/`, `infra/`↛`domains/`; Pydantic at boundaries only;
per-step loop: pyflakes + compileall + live import + F823-zero.
