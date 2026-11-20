# COELHO Nexus — Grafana Explore Query Reference

Datasource UIDs: **`mimir`** (PromQL) · **`loki`** (LogQL) · **`tempo`** (TraceQL)

All queries are copy-paste ready for the Grafana **Explore** page.
Select the datasource from the top-left dropdown, paste the query, and hit Run.

---

## Table of Contents

1. [Mimir — PromQL](#mimir--promql)
   - [YCS Retrieval](#ycs-retrieval)
   - [LLM Rotator](#llm-rotator)
   - [Service Graph](#service-graph)
   - [SLO / Health Snapshot](#slo--health-snapshot)
2. [Loki — LogQL](#loki--logql)
   - [Cross-Domain](#cross-domain)
   - [YCS](#ycs)
   - [LLM Rotator](#llm-rotator-1)
   - [Log Rates](#log-rates)
3. [Tempo — TraceQL](#tempo--traceql)
   - [Cross-Domain](#cross-domain-1)
   - [YCS Retrieval](#ycs-retrieval-1)
   - [LLM / Rotator](#llm--rotator)
   - [MCP Tools](#mcp-tools)

---

## Mimir — PromQL

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

**YCS grounded rate (target: >80%)**
```promql
sum(max_over_time(ycs_grounded_total[24h]))
/ clamp_min(sum(max_over_time(ycs_ask_run_total[24h])), 1) * 100
```

---

## Loki — LogQL

> Switch Loki to **"Code"** mode to paste these. Default time range: last 1h.
> All log panels have `detected_level` label — add `| detected_level = "error"` to filter severity.

### Cross-Domain

**All errors across every COELHO Nexus service**
```logql
{namespace=~"coelhonexus.*",
 service_name=~"coelhonexus-server|coelhonexus-celery|coelhonexus-fastmcp|coelhonexus-fasthtml"}
  |~ "(?i)(error|exception|traceback|critical|failed)"
```

**All errors with OTel trace link (server-side only)**
```logql
{namespace=~"coelhonexus.*",
 service_name=~"coelhonexus-server|coelhonexus-celery|coelhonexus-fastmcp|coelhonexus-fasthtml"}
  |~ "(?i)(error|exception|traceback|critical|failed)"
  | regexp "(?P<trace_id>(?i)trace_id=[0-9a-f]{32})"
```

**Orchestration workflow — task start / completion / failure**
```logql
{namespace=~"coelhonexus.*",
 service_name=~"coelhonexus-celery|coelhonexus-fastmcp"}
  |~ "(?i)(bd|distill|ycs|ask|scan|task|pipeline)"
```

**Error rate over time (all services)**
```logql
sum by (service_name) (
  count_over_time(
    {namespace=~"coelhonexus.*",
     service_name=~"coelhonexus-server|coelhonexus-celery|coelhonexus-fastmcp"}
    |~ "(?i)(error|exception|traceback|critical)"
    [5m]
  )
)
```

---

### YCS

**YCS ask completions**
```logql
{namespace=~"coelhonexus.*",
 service_name=~"coelhonexus-server|coelhonexus-celery"}
  |~ "(?i)(ycs|youtube|ask)"
  |~ "(?i)(done|complete|answer)"
```

**YCS retrieval errors (Qdrant / Neo4j / Elasticsearch)**
```logql
{namespace=~"coelhonexus.*",
 service_name=~"coelhonexus-server|coelhonexus-celery"}
  |~ "(?i)(qdrant|neo4j|elasticsearch)"
  |~ "(?i)(error|failed|exception|timeout)"
```

**YCS ask with OTel trace link**
```logql
{namespace=~"coelhonexus.*", service_name="coelhonexus-server"}
  |~ "(?i)(ycs|ask)"
  | regexp "(?P<trace_id>trace_id=[0-9a-f]{32})"
```

**YCS error rate over time**
```logql
sum (
  count_over_time(
    {namespace=~"coelhonexus.*",
     service_name=~"coelhonexus-server|coelhonexus-celery"}
    |~ "(?i)(ycs|youtube|ask|qdrant|neo4j)"
    |~ "(?i)(error|failed|exception)"
    [5m]
  )
)
```

---

### LLM Rotator

**Rotator discovery errors**
```logql
{namespace=~"coelhonexus.*",
 service_name=~"coelhonexus-server|coelhonexus-celery"}
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
     service_name=~"coelhonexus-server|coelhonexus-celery"}
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
     service_name=~"coelhonexus-server|coelhonexus-celery|coelhonexus-fastmcp"}
    [5m]
  )
)
```

**Error spike detector — only errors, 1-minute buckets**
```logql
sum by (service_name) (
  count_over_time(
    {namespace=~"coelhonexus.*",
     service_name=~"coelhonexus-server|coelhonexus-celery|coelhonexus-fastmcp"}
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

### LLM / Rotator

**All gen_ai.chat spans (every LLM completion)**
```traceql
{ name="gen_ai.chat" }
```

**Slow LLM calls (>30s — outlier detection)**
```traceql
{ name="gen_ai.chat" && duration>30s }
```

**LLM calls from Celery only**
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
