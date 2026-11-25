/** Read-only overview of the shared retrieval tools available to MCP clients. */

const MCP_TOOLS = [
  {
    name: "bd_search_elasticsearch",
    specialist: "ElasticSearch DeepAgent",
    description: "Bounded full-text evidence from the requested book and published revision.",
  },
  {
    name: "bd_search_qdrant",
    specialist: "Qdrant DeepAgent",
    description: "Dense and sparse hybrid evidence from the requested book and published revision.",
  },
  {
    name: "bd_search_neo4j",
    specialist: "Neo4j DeepAgent",
    description: "Read-only entity and relationship evidence from the requested book graph.",
  },
  {
    name: "bd_search_web",
    specialist: "Search MCP DeepAgent",
    description: "Optional external search through the selected Settings connection.",
  },
] as const;

export function McpToolsCatalog() {
  return (
    <details className="rounded-sm border border-border bg-card">
      <summary className="flex min-h-10 cursor-pointer flex-wrap items-center justify-between gap-x-3 gap-y-1 px-3 py-2 text-sm font-medium focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring">
        <span>Shared MCP retrieval tools</span>
        <span className="text-xs font-normal text-muted-foreground">
          {MCP_TOOLS.length} bounded, read-only tools
        </span>
      </summary>
      <div className="border-t border-border px-3 py-3">
        <p className="mb-3 text-xs leading-relaxed text-muted-foreground">
          MCP clients can use these evidence tools when the server deployment profile exposes them. Each
          retrieval specialist receives only its own tool. External search is optional. This editor remains
          the raw-query workbench.
        </p>
        <ul className="m-0 list-none p-0">
          {MCP_TOOLS.map((tool) => (
            <li
              key={tool.name}
              className="grid grid-cols-1 gap-x-4 gap-y-1 border-t border-border py-2 sm:grid-cols-[minmax(12rem,1fr)_minmax(10rem,0.8fr)_minmax(0,2fr)] sm:items-start"
            >
              <code className="break-all font-mono text-xs">{tool.name}</code>
              <span className="text-xs text-muted-foreground">{tool.specialist}</span>
              <span className="text-xs leading-relaxed text-muted-foreground">{tool.description}</span>
            </li>
          ))}
        </ul>
      </div>
    </details>
  );
}
