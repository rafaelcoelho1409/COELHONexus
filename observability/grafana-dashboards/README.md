# Grafana dashboards — observability of LLM + KD pipeline

Three importable Grafana dashboard JSONs that visualize:

- `rotator-red.json` — RED (rate / errors / duration) per LLM provider plus
  token usage + cost. Built on the OTel `gen_ai.*` metrics that LiteLLM v2's
  OTel callback emits via `infra/otel/exporters.py:add_metric_exporter`.
- `dd-pipeline.json` — DD synth health: chapter outcomes, refiner iterations,
  per-dim grader scores, audit missing-hash ratio. Built on the
  `kd_*` instruments declared in `apps/fastapi/infra/otel/metrics_registry.py`.
- `rr-digests.json` — Research Radar agent health: scan duration, digest
  size, tool-call distribution. Derived from RR trace spans + `gen_ai.*`
  metrics.

## How to import

1. Grafana UI → **Dashboards → New → Import → Upload JSON**, pick one of
   the files in this directory.
2. Pick the Mimir Prometheus datasource (or the equivalent name in your
   deploy — check `~/COELHOCloud/infrastructure/modules/grafana/` for the
   exact configured datasource UID).
3. Save. The dashboard variables ($provider, $framework) populate from the
   metrics within ~30 s of import.

## Where these should live long-term

The canonical home is COELHO Cloud:
```
~/COELHOCloud/infrastructure/modules/grafana/dashboards/
```
This repo's copy is the **source of truth for the JSON** — move + git-mv
into COELHOCloud when promoting. Update Grafana's provisioning sidecar
to pick them up automatically (no UI import on each deploy).

## Datasource notes

- Metric source: **Mimir** (Prometheus-compatible) via Alloy OTLP →
  remote-write. Datasource UID typically `mimir` or `prometheus` in the
  shared homelab Grafana.
- Trace source for exemplar links: **Tempo**. The histogram panels include
  `exemplar` config so clicking a p95 spike jumps directly to the matching
  trace in Tempo.
- Log source for the trace-log correlation: **Loki**. `LoggingInstrumentor`
  injects `trace_id` into every log record, so the Tempo trace view shows
  associated Loki logs in the right pane.
