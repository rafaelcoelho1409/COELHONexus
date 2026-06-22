# Observability Handoff — 2026-06-22

Short handoff note for the current LangFuse + OpenTelemetry state after the
Planner tracing fix on local `coelhonexus k3d`.

## 1. Current verified state

### Planner tracing fix

Applied code changes:

- `apps/fastapi/infra/otel/service.py`
  - removed LiteLLM string callback wiring for `langfuse_otel`
  - kept only `cost_callback`
- `apps/fastapi/domains/llm/rotator/observability/service.py`
  - added `is_recording()` guards before span attribute/status/error writes
- `apps/fastapi/infra/langfuse/annotation.py`
  - added closed-span guard before review annotations

Fresh verified Planner run:

- Date: `2026-06-22`
- Thread: `docs-distiller/skaffold/fb1c18be-dceb-402c-b339-7a98651ce55f`
- Celery task id: `7d612f46-d8d9-4f72-859f-a7081f51753d`
- LangFuse trace id: `18ae0899fc56e49f083cf6ef1a46fec8`

Verified outcomes:

- Planner run completed successfully
- No fresh `Setting attribute on ended span` / `Tried calling set_status on an ended span`
  warnings appeared for that new run
- LangFuse trace is no longer effectively empty
- Trace has real `input` and `output`
- Session is correctly grouped under the planner thread id
- `gen_ai.chat` observations are present and non-null

Important nuance:

- The generic Celery auto-instrumented observation
  `run/domains.dd.planner.task.run_planner` still shows null input/output
- The workflow observation `dd.planner.run` is the real non-null root for the
  planner workflow
- This is acceptable for now; the broken part was the planner workflow trace,
  not the generic Celery wrapper

### LangFuse infra state

Still observed:

- LangFuse worker logs show repeated Redis socket timeouts
- Planner trace volume is high: the fresh planner trace had `2731` observations
- LangFuse is usable, but still noisy and operationally weaker than ideal

## 2. Best next steps, in order

1. **Normalize Synth tracing to the same contract**
   - same ownership model as Planner
   - confirm non-null workflow root
   - confirm no ended-span warnings

2. **Reduce LangFuse trace volume**
   - keep workflow span + graph node spans + `gen_ai.chat`
   - reduce low-value infra noise, especially Redis chatter

3. **Fix LangFuse worker Redis timeouts**
   - this is the main remaining LangFuse infra risk

4. **Validate YCS and RR through LangFuse API**
   - one fresh run each
   - confirm:
     - non-null workflow trace
     - `gen_ai.chat` observations present
     - provider/model/tokens visible

5. **Add an observability smoke script**
   - query latest DD / YCS / RR traces from LangFuse API
   - fail if workflow root input/output is null
   - fail if no `gen_ai.chat` observations exist

6. **Clean LangFuse UX quality**
   - compact metadata only
   - short input/output summaries
   - predictable trace/session naming

7. **Revisit OTel filtering**
   - decide which auto-instrumented infra spans should remain in LangFuse
   - keep LGTM richer if needed, while keeping LangFuse cleaner

## 3. Recommended execution order

Highest-value sequence:

1. Synth tracing parity
2. LangFuse worker Redis fix
3. YCS / RR validation
4. LangFuse API smoke checks
5. Trace-noise reduction

## 4. Useful trace identifiers from this debugging session

- Fresh good Planner trace:
  - `18ae0899fc56e49f083cf6ef1a46fec8`
- Fresh good Planner thread:
  - `docs-distiller/skaffold/fb1c18be-dceb-402c-b339-7a98651ce55f`

## 5. Suggested first check when resuming work

Before editing more code:

1. trigger one fresh Synth run
2. query LangFuse API directly
3. confirm whether Synth has the same non-null workflow behavior now that
   Planner is fixed

