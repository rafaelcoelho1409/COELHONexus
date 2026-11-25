# BD Distill follow-up: retrieval after Agentic RAG

## Reminder

When Rafael finishes a full BD Distill pipeline run and returns to discuss next steps, revisit this idea together with other improvement ideas.

## Idea to evaluate

Have BD Distill retrieve evidence after BD Agentic RAG has completed for the same Ingestion run and processed-book revision. Add separate LangGraph nodes for:

- Elasticsearch retrieval
- Qdrant retrieval
- Neo4j retrieval
- Search MCP endpoint retrieval

Discuss how to combine and verify the results, preserve page-level source citations, and use the evidence to improve chapter coverage, explanations, and recommendations. Evaluate whether these retrieval steps complement the existing full-source Distill process without weakening completeness or source grounding.

## Trigger for implementation discussion

First review the output and gaps from a complete BD Distill run; then decide whether this retrieval integration and other proposed improvements would materially improve the final report.
