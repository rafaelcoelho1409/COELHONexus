# Grafana dashboards — COELHO Nexus application observability

Seven importable Grafana dashboard JSONs that visualize the app-level metrics
confirmed in Mimir for COELHO Nexus:

- `overview.json` — top-level cross-application RED-style view for Docs
  Distiller, YouTube Content Search, and Research Radar.
- `investigations.json` — mixed Mimir + Loki investigation surface for
  recent errors, orchestration logs, and trace-oriented troubleshooting.
- `service-topology.json` — service-edge view built on Tempo
  `traces_service_graph_*` metrics written to Mimir by Alloy.
- `rotator-red.json` — LLM rotator discovery + benchmark health, built on the
  `dd_rotator_*` metrics emitted by the benchmark and discovery services.
- `dd-pipeline.json` — Docs Distiller planner + study health, built on the
  live `dd_*` instruments declared in
  `apps/fastapi/infra/otel/metrics_registry.py`.
- `rr-digests.json` — Research Radar scan health, built on the live `rr_*`
  metrics emitted by the RR runtime.
- `ycs-retrieval.json` — YouTube Content Search ask/retrieval health, built on
  the live `ycs_*` metrics emitted by the YCS runtime.

Several dashboards also include small Loki logs panels for fast error
inspection, while the heavier trace/log investigation flow is centralized in
`investigations.json`.

## How to import

1. Grafana UI → **Dashboards → New → Import → Upload JSON**, pick one of
   the files in this directory.
2. Pick the Mimir Prometheus datasource (or the equivalent name in your
   deploy — check `~/COELHOCloud/infrastructure/modules/grafana/` for the
   exact configured datasource UID).
3. Save. The dashboard variables populate from the metrics within ~30 s of
   import.

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
  remote-write. Datasource UID in these JSON files is `mimir`.
- Trace source for exemplar links: **Tempo**. The latency-focused panels in
  these dashboards enable exemplars where the underlying metrics support them.
- Trace/log/metric navigation is primarily handled by the provisioned Tempo
  datasource, which already wires Tempo to Loki (`tracesToLogsV2`) and Mimir
  (`tracesToMetrics`, `serviceMap`, `nodeGraph`).

## Provisioning

When Helm `observability.enabled=true`, this repo also provisions these JSONs
as `grafana_dashboard=1` ConfigMaps so Grafana's sidecar can pick them up
automatically across namespaces.
