import type { FlowEdgeSpec, FlowStage } from "./flow-layout";

const VIRTUAL_NODES = new Set(["__start__", "__end__"]);

export type CompiledTopology = {
  nodeIds: string[];
  edges: FlowEdgeSpec[];
};

/** Read real node declarations and direct connections from LangGraph's generated Mermaid source. */
export function compiledTopology(
  source: string | null | undefined,
  stage: FlowStage,
): CompiledTopology | null {
  if (!source) return null;

  const nodeIds: string[] = [];
  const connections: Array<{ source: string; target: string }> = [];
  for (const line of source.split(/\r?\n/u)) {
    const declaration = line.match(/^\s*([A-Za-z_]\w*)\s*(?=\(|\[|\{)/u)?.[1];
    if (declaration && !VIRTUAL_NODES.has(declaration) && !nodeIds.includes(declaration)) {
      nodeIds.push(declaration);
    }

    const edge = line.match(/^\s*([A-Za-z_]\w*)\s+(.+?)\s+([A-Za-z_]\w*)\s*;?\s*$/u);
    const from = edge?.[1];
    const connector = edge?.[2];
    const to = edge?.[3];
    if (from && connector?.includes(">") && /[-=]/u.test(connector) && to) {
      connections.push({ source: from, target: to });
    }
  }

  if (nodeIds.length === 0) return null;
  const declaredNodes = new Set(nodeIds);
  const edges = connections.flatMap((connection, index) => {
    if (!declaredNodes.has(connection.source) || !declaredNodes.has(connection.target)) return [];
    return [
      {
        id: `${connection.source}->${connection.target}:${index}`,
        ...connection,
        kind:
          stage === "synth" && connection.source === "mgsr_replan" && connection.target === "sawc_write"
            ? "loop" as const
            : "next" as const,
      },
    ];
  });
  return { nodeIds, edges };
}
