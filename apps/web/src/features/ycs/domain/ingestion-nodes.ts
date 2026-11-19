/** Operational resources assigned to each YCS ingestion graph node. */

export type IngestionNodeResources = {
  agents: readonly string[];
  skills: readonly string[];
  tools: readonly string[];
};

const NODE_RESOURCES = {
  playwright: {
    agents: ["None (deterministic step)"],
    skills: ["Collect video metadata", "Retrieve transcripts"],
    tools: ["yt-dlp", "Playwright"],
  },
  elasticsearch: {
    agents: ["None (deterministic step)"],
    skills: ["Index transcripts and video metadata", "Bulk document writes"],
    tools: ["Elasticsearch bulk API"],
  },
  qdrant: {
    agents: ["None (deterministic step)"],
    skills: ["Chunk transcripts", "Hybrid dense and BM25 indexing"],
    tools: ["Embedding service", "Qdrant upsert API"],
  },
  neo4j: {
    agents: ["None (fixed extraction component)"],
    skills: ["Extract entities and relationships", "Validate transcript evidence", "Write graph data"],
    tools: ["LLMGraphTransformer", "Neo4j"],
  },
} satisfies Record<string, IngestionNodeResources>;

export function ingestionNodeResources(nodeId: string): IngestionNodeResources | null {
  if (nodeId === "playwright" || nodeId === "elasticsearch" || nodeId === "qdrant" || nodeId === "neo4j") {
    return NODE_RESOURCES[nodeId];
  }
  return null;
}
