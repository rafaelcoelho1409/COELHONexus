/**
 * Page copy, kept apart from the components. Policy: this site describes what the products do and how the
 * platform is operated. It never names the model-routing layer, the model providers, or any private
 * project (`scripts/check-public-copy.mjs` fails the build if it does).
 */

export const NAV = [
  { label: "Products", href: "#products" },
  { label: "System", href: "#system" },
  { label: "Principles", href: "#principles" },
  { label: "Source", href: "#source" },
] as const;

export const PRODUCTS = [
  {
    index: "01",
    name: "Docs Distiller",
    summary:
      "Reads a framework's documentation, plans the chapters, and writes a coherent study guide you can read in order.",
    pipeline: ["Ingest", "Plan", "Synthesize", "Study"],
    stack: "LangGraph · MinIO · PostgreSQL",
  },
  {
    index: "02",
    name: "YouTube Content Search",
    summary:
      "Indexes a whole channel, then answers questions against the transcripts, the entities in them and the relationships between them — with citations.",
    pipeline: ["Transcribe", "Index", "Graph", "Ask"],
    stack: "Playwright · Qdrant · Neo4j",
  },
] as const;

export const LAYERS = [
  { index: "01", label: "Edge", nodes: ["Browser", "Next.js"], highlight: "Next.js" },
  { index: "02", label: "Core", nodes: ["FastAPI", "FastMCP", "Celery"] },
  { index: "03", label: "Pipelines", nodes: ["LangGraph", "Playwright", "Crawl4AI"] },
  { index: "04", label: "Data", nodes: ["PostgreSQL", "Neo4j", "Qdrant", "Elasticsearch", "MinIO", "Redis"] },
  { index: "05", label: "Observability", nodes: ["OpenTelemetry", "Grafana LGTM", "LangFuse"] },
  { index: "06", label: "Delivery", nodes: ["Terraform", "Terragrunt", "k3d", "Helm", "ArgoCD", "Skaffold"] },
] as const;

export const PRINCIPLES = [
  {
    index: "01",
    title: "Replayable by design.",
    body: "Content-addressed artifacts and checkpointed graphs resume at the step that broke, not from zero. Replaying a run costs nothing it already paid for.",
  },
  {
    index: "02",
    title: "Observed end to end.",
    body: "Every pipeline, token and span flows through OpenTelemetry into Grafana's LGTM stack and LangFuse. If it ran, there is a trace.",
  },
  {
    index: "03",
    title: "Operated as code.",
    body: "The cluster, the services and this interface are provisioned and deployed from one repository. Nothing is clicked together.",
  },
] as const;

export const QUICKSTART = [
  "git clone https://github.com/rafaelcoelho1409/COELHONexus.git",
  "cd COELHONexus/infrastructure/live/coelhonexus",
  "terragrunt run --all apply --non-interactive --parallelism 1",
] as const;
