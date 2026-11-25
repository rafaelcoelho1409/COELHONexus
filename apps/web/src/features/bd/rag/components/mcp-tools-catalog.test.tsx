import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { McpToolsCatalog } from "./mcp-tools-catalog";

describe("MCP retrieval tools catalog", () => {
  it("shows all four bounded tools and their specialists", () => {
    render(<McpToolsCatalog />);

    expect(screen.getByText("Shared MCP retrieval tools")).toBeInTheDocument();
    expect(screen.getByText("bd_search_elasticsearch")).toBeInTheDocument();
    expect(screen.getByText("bd_search_qdrant")).toBeInTheDocument();
    expect(screen.getByText("bd_search_neo4j")).toBeInTheDocument();
    expect(screen.getByText("bd_search_web")).toBeInTheDocument();
    expect(screen.getByText("Search MCP DeepAgent")).toBeInTheDocument();
    expect(screen.getByText(/deployment profile exposes them/)).toBeInTheDocument();
  });
});
