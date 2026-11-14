"use client";

import { useIngestionGraph, useStageInfoQuery } from "../hooks/use-stage-queries";
import { LangGraphTopology } from "./langgraph-topology";

type CompiledGraphOnlyProps = {
  stage: "ingestion" | "planner" | "synth";
};

function IngestionGraph() {
  const graph = useIngestionGraph();

  return <LangGraphTopology title="Ingestion" source={graph.source} loading={graph.loading} />;
}

function PipelineGraph({ stage }: { stage: "planner" | "synth" }) {
  const query = useStageInfoQuery(stage);
  const title = stage === "planner" ? "Planner" : "Synth";

  return <LangGraphTopology title={title} source={query.data?.graph_mermaid ?? null} loading={query.isPending} />;
}

export function CompiledGraphOnly({ stage }: CompiledGraphOnlyProps) {
  return (
    <main id="main" className="min-h-screen min-w-0 bg-background p-4">
      {stage === "ingestion" ? <IngestionGraph /> : <PipelineGraph stage={stage} />}
    </main>
  );
}
