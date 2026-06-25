# COELHO Nexus — Grafana Explore Query Reference

Datasource UIDs: **`mimir`** (PromQL) · **`loki`** (LogQL) · **`tempo`** (TraceQL)

All queries are copy-paste ready for the Grafana **Explore** page.
Select the datasource from the top-left dropdown, paste the query, and hit Run.

---

## Table of Contents

1. [Mimir — PromQL](#mimir--promql)
   - [DD Planner](#dd-planner)
   - [DD Synth](#dd-synth)
   - [YCS Retrieval](#ycs-retrieval)
   - [Research Radar (RR)](#research-radar-rr)
   - [LLM Rotator](#llm-rotator)
   - [Service Graph](#service-graph)
   - [SLO / Health Snapshot](#slo--health-snapshot)
2. [Loki — LogQL](#loki--logql)
   - [Cross-Domain](#cross-domain)
   - [DD Planner & Synth](#dd-planner--synth)
   - [YCS](#ycs)
   - [Research Radar (RR)](#research-radar-rr-1)
   - [LLM Rotator](#llm-rotator-1)
   - [Log Rates](#log-rates)
3. [Tempo — TraceQL](#tempo--traceql)
   - [Cross-Domain](#cross-domain-1)
   - [DD Planner & Synth](#dd-planner--synth-1)
   - [YCS Retrieval](#ycs-retrieval-1)
   - [Research Radar (RR)](#research-radar-rr-2)
   - [LLM / Rotator](#llm--rotator)
   - [MCP Tools](#mcp-tools)

---

## Mimir — PromQL

### DD Planner

**Planner run rate by outcome**
```promql
sum by (outcome, framework) (rate(dd_planner_run_total[30m]))
```

**Planner p50 / p95 / p99 wall time**
```promql
histogram_quantile(0.95, sum by (le, framework) (rate(dd_planner_run_duration_seconds_bucket[1h])))
```

**Planner daily totals (survives pod restarts)**
```promql
sum by (outcome, framework) (max_over_time(dd_planner_run_total[24h]))
```

**Chapter count p95 per planner run**
```promql
histogram_quantile(0.95, sum by (le, framework) (rate(dd_planner_chapter_count_bucket[24h])))
```

**Non-done planner share (error / cancelled rate)**
```promql
sum(rate(dd_planner_run_total{outcome!="done"}[1h]))
/ clamp_min(sum(rate(dd_planner_run_total[1h])), 0.001) * 100
```

---

### DD Synth

**Chapter outcome rate (accept vs debt_below)**
```promql
sum by (outcome, framework) (rate(dd_chapter_outcome_total[30m]))
```

**Chapter synth p95 wall time**
```promql
histogram_quantile(0.95, sum by (le, framework) (rate(dd_chapter_synth_duration_seconds_bucket[1h])))
```

**Self-Refine iterations to accept — p50 / p95**
```promql
histogram_quantile(0.95, sum by (le, framework) (rate(dd_refiner_iters_to_accept_bucket[1h])))
```

**Grader score by dimension (mean)**
```promql
sum by (dim) (rate(dd_classical_grader_dim_score_sum[1h]))
/ clamp_min(sum by (dim) (rate(dd_classical_grader_dim_score_count[1h])), 0.001)
```

**Classical patch applications by dimension**
```promql
sum by (dim, framework) (rate(dd_classical_patch_applied_total[1h]))
```

**Audit missing-hash ratio — mean per iteration**
```promql
sum by (framework) (rate(dd_audit_missing_hashes_ratio_sum[1h]))
/ clamp_min(sum by (framework) (rate(dd_audit_missing_hashes_ratio_count[1h])), 0.001)
```

**Bucket split overflow events (section-cap breaches)**
```promql
sum by (framework, sections_dropped) (rate(dd_bucket_split_overflow_total[1h]))
```

**Study completion p95 (ingest → assembler, end-to-end)**
```promql
histogram_quantile(0.95, sum by (le, framework) (rate(dd_study_completion_seconds_bucket[1h])))
```

**Studies completed (daily)**
```promql
sum by (framework, outcome) (max_over_time(dd_study_completion_seconds_count[24h]))
```

---

### YCS Retrieval

**Ask run rate by route and outcome**
```promql
sum by (route, mode, outcome) (rate(ycs_ask_run_total[30m]))
```

**Ask p95 wall time by mode**
```promql
histogram_quantile(0.95, sum by (le, mode) (rate(ycs_ask_run_duration_seconds_bucket[1h])))
```

**Grounded answer rate (%)**
```promql
sum(rate(ycs_grounded_total[1h]))
/ clamp_min(sum(rate(ycs_ask_run_total[1h])), 0.001) * 100
```

**Docs retrieved vs graded — retrieval funnel**
```promql
sum by (mode) (rate(ycs_retrieved_docs_sum[30m]))
/ clamp_min(sum by (mode) (rate(ycs_retrieved_docs_count[30m])), 0.001)
```
```promql
sum by (mode) (rate(ycs_graded_docs_sum[30m]))
/ clamp_min(sum by (mode) (rate(ycs_graded_docs_count[30m])), 0.001)
```

**Citation count p95 per answer**
```promql
histogram_quantile(0.95, sum by (le, mode) (rate(ycs_citation_count_bucket[1h])))
```

**Query rewrite rate**
```promql
sum(rate(ycs_rewrite_total[30m]))
```

**Sub-question outcomes (deep mode)**
```promql
sum by (outcome) (rate(ycs_subquestion_total[30m]))
```

---

### Research Radar (RR)

**Scan run rate by outcome and degradation flag**
```promql
sum by (outcome, degraded) (rate(rr_scan_run_total[1h]))
```

**Scan p95 wall time**
```promql
histogram_quantile(0.95, sum by (le) (rate(rr_scan_run_duration_seconds_bucket[24h])))
```

**Degraded scan share (%)**
```promql
sum(rate(rr_scan_run_total{degraded="true"}[24h]))
/ clamp_min(sum(rate(rr_scan_run_total[24h])), 0.001) * 100
```

**Mean findings per scan**
```promql
sum(rate(rr_findings_sum[24h]))
/ clamp_min(sum(rate(rr_findings_count[24h])), 0.001)
```

**Triage pass rate — findings / candidates (%)**
```promql
sum(rate(rr_findings_sum[24h]))
/ clamp_min(sum(rate(rr_candidates_sum[24h])), 0.001) * 100
```

**Themes per scan (mean)**
```promql
sum(rate(rr_theme_count_sum[24h]))
/ clamp_min(sum(rate(rr_theme_count_count[24h])), 0.001)
```

**Phase event count by phase**
```promql
sum by (phase) (max_over_time(rr_phase_event_total[24h]))
```

---

### LLM Rotator

**Alive models by provider (current)**
```promql
sum by (provider) (max_over_time(dd_rotator_models_alive[1h]))
```

**Benchmark fetch outcomes by source**
```promql
sum by (source, outcome) (max_over_time(dd_rotator_benchmark_fetch_total[24h]))
```

**Benchmark cache hit layers**
```promql
sum by (layer) (max_over_time(dd_rotator_benchmark_cache_hit_total[24h]))
```

**Canonical model resolution by layer**
```promql
sum by (layer) (max_over_time(dd_rotator_canonical_resolution_total[24h]))
```

**Rotator discovery errors by provider**
```promql
sum by (provider, error_type) (max_over_time(dd_rotator_discovery_error_total[24h]))
```

**Rotator discovery p95 duration**
```promql
histogram_quantile(0.95, sum by (le) (rate(dd_rotator_discovery_duration_seconds_bucket[1h])))
```

---

### Service Graph

**Top request-rate edges between services**
```promql
topk(10, sum by (client, server) (rate(traces_service_graph_request_total[5m])))
```

**Top failing edges**
```promql
topk(10, sum by (client, server) (rate(traces_service_graph_request_failed_total[5m])))
```

**Cross-service latency p95 by edge**
```promql
topk(10,
  histogram_quantile(0.95,
    sum by (client, server, le) (rate(traces_service_graph_request_client_seconds_bucket[5m]))
  )
)
```

**Error rate per edge (%)**
```promql
sum by (client, server) (rate(traces_service_graph_request_failed_total[5m]))
/ clamp_min(sum by (client, server) (rate(traces_service_graph_request_total[5m])), 0.001) * 100
```

---

### SLO / Health Snapshot

> Instant values — paste one at a time in Explore for a quick health check.

**DD Planner p99 latency (target: <600s)**
```promql
histogram_quantile(0.99, sum by (le) (rate(dd_planner_run_duration_seconds_bucket[24h])))
```

**YCS grounded rate (target: >80%)**
```promql
sum(max_over_time(ycs_grounded_total[24h]))
/ clamp_min(sum(max_over_time(ycs_ask_run_total[24h])), 1) * 100
```

**RR degraded scan rate (target: <10%)**
```promql
sum(max_over_time(rr_scan_run_total{degraded="true"}[24h]))
/ clamp_min(sum(max_over_time(rr_scan_run_total[24h])), 1) * 100
```

**RR mean findings per scan (target: >3)**
```promql
sum(max_over_time(rr_findings_sum[24h]))
/ clamp_min(sum(max_over_time(rr_findings_count[24h])), 1)
```

---

## Loki — LogQL

> Switch Loki to **"Code"** mode to paste these. Default time range: last 1h.
> All log panels have `detected_level` label — add `| detected_level = "error"` to filter severity.

### Cross-Domain

**All errors across every COELHO Nexus service**
```logql
{namespace=~"coelhonexus.*",
 service_name=~"coelhonexus-fastapi|coelhonexus-celery|coelhonexus-fastmcp|coelhonexus-fasthtml"}
  |~ "(?i)(error|exception|traceback|critical|failed)"
```

**All errors with OTel trace link (FastAPI-side only)**
```logql
{namespace=~"coelhonexus.*",
 service_name=~"coelhonexus-fastapi|coelhonexus-celery|coelhonexus-fastmcp|coelhonexus-fasthtml"}
  |~ "(?i)(error|exception|traceback|critical|failed)"
  | regexp "(?P<trace_id>(?i)trace_id=[0-9a-f]{32})"
```

**Orchestration workflow — task start / completion / failure**
```logql
{namespace=~"coelhonexus.*",
 service_name=~"coelhonexus-celery|coelhonexus-fastmcp"}
  |~ "(?i)(planner|synth|study|ycs|ask|rr|radar|scan|task|pipeline)"
```

**Error rate over time (all services)**
```logql
sum by (service_name) (
  count_over_time(
    {namespace=~"coelhonexus.*",
     service_name=~"coelhonexus-fastapi|coelhonexus-celery|coelhonexus-fastmcp"}
    |~ "(?i)(error|exception|traceback|critical)"
    [5m]
  )
)
```

---

### DD Planner & Synth

**Planner run completions (done / cancelled / failed)**
```logql
{namespace=~"coelhonexus.*", service_name="coelhonexus-celery"}
  |~ "\\[planner\\]"
  |~ "(?i)(done|cancelled|failed)"
```

**Planner node failures with error type**
```logql
{namespace=~"coelhonexus.*", service_name="coelhonexus-celery"}
  |~ "\\[planner\\]"
  |~ "run failed"
```

**Planner catch-up recovery events (missing nodes)**
```logql
{namespace=~"coelhonexus.*", service_name="coelhonexus-celery"}
  |~ "catch-up ran missing node"
```

**Study orchestrator — chapter status stream**
```logql
{namespace=~"coelhonexus.*", service_name="coelhonexus-celery"}
  |~ "\\[study-orchestrator\\]"
```

**Study completion summary (done + chapter counts)**
```logql
{namespace=~"coelhonexus.*", service_name="coelhonexus-celery"}
  |~ "\\[study-orchestrator\\]"
  |~ "done"
  | regexp "(?P<slug>[a-z][a-z0-9_-]{2,}) n_completed=(?P<n_completed>\\d+)"
```

**book_harmonize cache hits (cross-chapter coherence)**
```logql
{namespace=~"coelhonexus.*", service_name="coelhonexus-celery"}
  |~ "\\[book_harmonize\\]"
  |~ "CACHE HIT"
```

**Synth chapter crash events**
```logql
{namespace=~"coelhonexus.*", service_name="coelhonexus-celery"}
  |~ "\\[synth\\]"
  |~ "run failed"
```

**DD chapter synth rate over time**
```logql
sum by (detected_level) (
  count_over_time(
    {namespace=~"coelhonexus.*", service_name="coelhonexus-celery"}
    |~ "(?i)(planner|synth|study|chapter)"
    [5m]
  )
)
```

---

### YCS

**YCS ask completions**
```logql
{namespace=~"coelhonexus.*",
 service_name=~"coelhonexus-fastapi|coelhonexus-celery"}
  |~ "(?i)(ycs|youtube|ask)"
  |~ "(?i)(done|complete|answer)"
```

**YCS retrieval errors (Qdrant / Neo4j / Elasticsearch)**
```logql
{namespace=~"coelhonexus.*",
 service_name=~"coelhonexus-fastapi|coelhonexus-celery"}
  |~ "(?i)(qdrant|neo4j|elasticsearch)"
  |~ "(?i)(error|failed|exception|timeout)"
```

**YCS ask with OTel trace link**
```logql
{namespace=~"coelhonexus.*", service_name="coelhonexus-fastapi"}
  |~ "(?i)(ycs|ask)"
  | regexp "(?P<trace_id>trace_id=[0-9a-f]{32})"
```

**YCS error rate over time**
```logql
sum (
  count_over_time(
    {namespace=~"coelhonexus.*",
     service_name=~"coelhonexus-fastapi|coelhonexus-celery"}
    |~ "(?i)(ycs|youtube|ask|qdrant|neo4j)"
    |~ "(?i)(error|failed|exception)"
    [5m]
  )
)
```

---

### Research Radar (RR)

**Scan start and completion (with scan_id extraction)**
```logql
{namespace=~"coelhonexus.*", service_name="coelhonexus-celery"}
  |~ "\\[rr-task\\]"
  |~ "(?i)(run_radar_scan|DONE)"
  | regexp "(?P<scan_id>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
```

**Specific scan — all logs for one scan_id** (replace UUID)
```logql
{namespace=~"coelhonexus.*",
 service_name=~"coelhonexus-celery|coelhonexus-fastmcp"}
  |~ "6abc1eb0-577b-41da-87f4-deee40b94c04"
```

**Degraded scan completions**
```logql
{namespace=~"coelhonexus.*", service_name="coelhonexus-celery"}
  |~ "\\[rr-task\\]"
  |~ "degraded=True"
```

**Backfill recovery events**
```logql
{namespace=~"coelhonexus.*", service_name="coelhonexus-celery"}
  |~ "\\[rr-task\\]"
  |~ "backfill"
```

**Discovery tool errors (arxiv 429, HN 400, S2 429)**
```logql
{namespace=~"coelhonexus.*", service_name="coelhonexus-fastmcp"}
  |~ "(?i)(429|400|rate.exceeded|too.many.requests|toolerror)"
```

**Phase enforcer nudges (orchestrator loop diagnosis)**
```logql
{namespace=~"coelhonexus.*", service_name="coelhonexus-celery"}
  |~ "\\[phase-enforcer\\]"
  | regexp "(?P<scan_id>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
```

**MCP tool calls — all tool dispatches for RR**
```logql
{namespace=~"coelhonexus.*", service_name="coelhonexus-fastmcp"}
  |~ "(?i)(arxiv|semantic.scholar|huggingface|hn_search|triage|deep.read|synthesis)"
```

**RR scan error rate over time**
```logql
sum (
  count_over_time(
    {namespace=~"coelhonexus.*",
     service_name=~"coelhonexus-celery|coelhonexus-fastmcp"}
    |~ "(?i)(radar|scan|rr)"
    |~ "(?i)(error|failed|exception|traceback)"
    [5m]
  )
)
```

---

### LLM Rotator

**Rotator discovery errors**
```logql
{namespace=~"coelhonexus.*",
 service_name=~"coelhonexus-fastapi|coelhonexus-celery"}
  |~ "(?i)(rotator|benchmark|canonical)"
  |~ "(?i)(error|failed|exception)"
```

**Provider selection logs (which arm the bandit picked)**
```logql
{namespace=~"coelhonexus.*", service_name="coelhonexus-celery"}
  |~ "\\[llm-chain\\]"
```

**Rotator error rate by provider over time**
```logql
sum by (detected_level) (
  count_over_time(
    {namespace=~"coelhonexus.*",
     service_name=~"coelhonexus-fastapi|coelhonexus-celery"}
    |~ "(?i)(rotator|benchmark|canonical|discovery)"
    [5m]
  )
)
```

---

### Log Rates

**All services — log rate by severity (for anomaly detection)**
```logql
sum by (service_name, detected_level) (
  count_over_time(
    {namespace=~"coelhonexus.*",
     service_name=~"coelhonexus-fastapi|coelhonexus-celery|coelhonexus-fastmcp"}
    [5m]
  )
)
```

**Error spike detector — only errors, 1-minute buckets**
```logql
sum by (service_name) (
  count_over_time(
    {namespace=~"coelhonexus.*",
     service_name=~"coelhonexus-fastapi|coelhonexus-celery|coelhonexus-fastmcp"}
    |~ "(?i)(error|exception|traceback|critical)"
    [1m]
  )
)
```

---

## Tempo — TraceQL

> In Explore, select **Tempo** datasource. Switch to **Search** tab and paste into the **TraceQL** field.
> Time range applies to trace start time.

### Cross-Domain

**All recent COELHO Nexus traces**
```traceql
{ resource.service.name=~"coelhonexus.*" }
```

**All error-status traces**
```traceql
{ resource.service.name=~"coelhonexus.*" && status=error }
```

**All traces from Celery workers (long-running tasks)**
```traceql
{ resource.service.name="coelhonexus-celery" }
```

**All traces from FastMCP (MCP tool calls)**
```traceql
{ resource.service.name="coelhonexus-fastmcp" }
```

---

### DD Planner & Synth

**All DD planner root spans**
```traceql
{ name="dd.planner.run" }
```

**DD planner node spans (individual LangGraph nodes)**
```traceql
{ name=~"dd.planner.node.*" }
```

**Failed DD planner node spans**
```traceql
{ name=~"dd.planner.node.*" && status=error }
```

**Planner spans for a specific framework** (replace slug)
```traceql
{ name="dd.planner.run" && span.planner.framework_slug="langchain-docs" }
```

**DD synth chapter root spans**
```traceql
{ name="dd.synth.chapter.run" }
```

**DD synth node spans — all nodes across chapters**
```traceql
{ name=~"dd.synth.node.*" }
```

**DD synth study root spans (multi-chapter orchestrator)**
```traceql
{ name="dd.synth.study.run" }
```

**book_harmonize spans (cross-chapter coherence pass)**
```traceql
{ name="dd.synth.node.book_harmonize" }
```

**Slow DD planner runs (>5 min)**
```traceql
{ name="dd.planner.run" && duration>5m }
```

---

### YCS Retrieval

**All YCS ask root spans**
```traceql
{ name=~"ycs.node.*" }
```

**YCS Qdrant retrieval spans**
```traceql
{ span.db.system="qdrant" }
```

**YCS Neo4j lookup spans**
```traceql
{ span.db.system="neo4j" }
```

**YCS Elasticsearch search spans**
```traceql
{ span.db.system="elasticsearch" }
```

**YCS reranking spans**
```traceql
{ name="gen_ai.rerank" }
```

**YCS smart fanout retriever spans**
```traceql
{ name="ycs.retriever.smart_fanout" }
```

**Slow YCS asks (>30s)**
```traceql
{ name=~"ycs.node.*" && duration>30s }
```

---

### Research Radar (RR)

**All RR scan root spans**
```traceql
{ name="rr.scan.run" }
```

**RR phase spans (all phases for all scans)**
```traceql
{ span.rr.phase=~".+" }
```

**RR phase spans for a specific scan_id** (replace UUID)
```traceql
{ name="rr.scan.run" && span.rr.scan_id="6abc1eb0-577b-41da-87f4-deee40b94c04" }
```

**RR discovery phase spans only**
```traceql
{ name="rr.node.discovery" }
```

**RR deep_read phase spans**
```traceql
{ name="rr.node.deep_read" }
```

**RR synthesis phase spans**
```traceql
{ name="rr.node.synthesis" }
```

**Degraded RR scans (backfill fired)**
```traceql
{ name="rr.node.backfill" }
```

**RR error traces (discovery 429s, tool failures)**
```traceql
{ resource.service.name=~"coelhonexus-celery|coelhonexus-fastmcp" && status=error }
```

**Slow RR scans (>5 min)**
```traceql
{ name="rr.scan.run" && duration>5m }
```

---

### LLM / Rotator

**All gen_ai.chat spans (every LLM completion)**
```traceql
{ name="gen_ai.chat" }
```

**Slow LLM calls (>30s — outlier detection)**
```traceql
{ name="gen_ai.chat" && duration>30s }
```

**LLM calls from Celery only (DD/RR domain)**
```traceql
{ resource.service.name="coelhonexus-celery" && name="gen_ai.chat" }
```

**Bandit cascade spans (rotator arm selection)**
```traceql
{ name="rotator.bandit_cascade" }
```

**Slow bandit cascades (cascade took >10s — arm exhaustion)**
```traceql
{ name="rotator.bandit_cascade" && duration>10s }
```

**Embedding spans**
```traceql
{ name="gen_ai.embed" }
```

**Reranking spans**
```traceql
{ name="gen_ai.rerank" }
```

**Failed LLM calls (errors in rotator cascade)**
```traceql
{ name="gen_ai.chat" && status=error }
```

---

### MCP Tools

**All MCP tool dispatch spans**
```traceql
{ name=~"mcp.tool.*" }
```

**MCP tool errors (arxiv 429, HN 400, S2 429)**
```traceql
{ name=~"mcp.tool.*" && status=error }
```

**arxiv_search tool spans**
```traceql
{ name="mcp.tool.arxiv_search" }
```

**semantic_scholar_search tool spans**
```traceql
{ name="mcp.tool.semantic_scholar_search" }
```

**hn_search tool spans**
```traceql
{ name="mcp.tool.hn_search" }
```

**huggingface_daily_papers tool spans**
```traceql
{ name="mcp.tool.huggingface_daily_papers_search" }
```

**Slow MCP tool calls (>10s — rate limit / timeout)**
```traceql
{ name=~"mcp.tool.*" && duration>10s }
```
