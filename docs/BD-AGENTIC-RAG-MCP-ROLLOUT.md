# BD Agentic RAG and FastMCP rollout

## Architecture

`/mcp` remains one shared FastMCP endpoint. Its BD tools are registered as one `bd_search` family:

- `bd_search_elasticsearch` for bounded BM25 passage retrieval.
- `bd_search_qdrant` for dense and sparse hybrid retrieval.
- `bd_search_neo4j` for fixed read-only entity and relationship retrieval.
- `bd_search_web` for the Settings-selected external Search MCP provider.

The three internal-store tools use the same current-index pointer and exact run and processed-book revision filters already used by BD Ask. They return evidence IDs, citations, and an explicit per-source status. The graph and MCP tools never accept raw Elasticsearch DSL, Cypher, arbitrary Qdrant filters, or arbitrary URLs.

The source-specialist graph fans out to four DeepAgents, each with only its source's MCP adapter tool. Their evidence is fused deterministically with reciprocal rank fusion; a specialist's prose is never treated as evidence. The Search MCP lane is optional because it uses an external provider and may incur cost.

The BD Agentic RAG graph package groups both workflows: indexing lives under `ai/bd/graphs/rag/index`, and source-specialist retrieval lives under `ai/bd/graphs/rag/retrieval`.

## Deployment profiles

`BD_MCP_PROFILE` defaults to `disabled`. In that profile, `/mcp` exposes only `ping`.

For a local demonstration, set `BD_MCP_PROFILE=local_demo` and `BD_MCP_DEMO_BOOK_IDS` to a comma-separated list containing only public or synthetic demo books. The app rejects this profile in production and the MCP ASGI app accepts loopback clients only. Settings-backed Search MCP remains disabled.

For a protected deployment, set `BD_MCP_PROFILE=protected` and configure:

```text
BD_MCP_JWKS_URI=https://identity.example/.well-known/jwks.json
BD_MCP_ISSUER=https://identity.example/
BD_MCP_AUDIENCE=coelho-nexus-mcp
BD_MCP_REQUIRED_SCOPE=bd.search.read
```

The JWT must contain `bd_book_ids: ["..."]` (or `bd: {"book_ids": ["..."]}`); each requested book must be listed. FastMCP validates the JWT signature, issuer, audience, and required scope. A gateway must preserve the verified bearer token and prevent direct access around the protected endpoint.

Settings-backed web search is off by default. Enabling it requires `BD_MCP_WEB_SEARCH_ENABLED=true`, the additional token scope `bd.search.web`, and Redis for a per-principal request budget of 10 searches per minute. This path uses the existing selected Settings connection; never enable it for a public demo that has access to private Settings credentials.

## Agent rollout

`BD_RAG_SPECIALIST_AGENTS_ENABLED` defaults to `false`. Set it to `true` in a local or internal canary to route BD Ask through the four-specialist graph. The current deterministic Ask retriever remains the default until an evaluation corpus shows improved recall and citation quality at an acceptable latency and model-call cost.

The graph can include Search MCP by passing `include_web=true`; callers must explicitly choose this because web evidence is external and may use a configured provider. BD Ask's existing external-search stage remains in place during the canary.

Distill's current Search MCP stage stays separate. It validates quotations against full source text, while the new web MCP tool returns bounded search snippets. Distill should receive cross-book retrieval only after the product has a tenant/corpus authorization contract and citation schema for internal RAG evidence. Searching the current book again would duplicate the source already used to build the guide and would not satisfy related-corpus retrieval.

BD Query's raw database workbench remains separate from the safe evidence tools. Query and other MCP clients can call the shared retrieval tools directly; raw query execution is not exposed through the RAG MCP surface.

## Compatibility and release sequence

The server dependency remains on the repository's FastMCP 3.x line (`>=3.4.10,<4.0`; lock resolves 3.4.11), and this feature is tested against that lock. As of 2026-11-24, FastMCP 4.2.5 is the latest stable release; its v4 migration removes APIs deprecated during v3 and updates protocol/model behavior. Run the official migration audit as a separate dependency change before changing the major pin. The repository also locks DeepAgents 0.6.8 and LangGraph 1.2.16; their current upstream releases are 0.7.41 and 1.2.24. Keep those upgrades separate too, then rerun the full server suite and MCP contract tests.

The version check was made against the [FastMCP release history](https://github.com/PrefectHQ/fastmcp/releases), [FastMCP 4 migration announcement](https://blog.gofastmcp.com/3mufbh2vcv22o), [DeepAgents changelog](https://github.com/langchain-ai/deepagents/blob/main/libs/deepagents/CHANGELOG.md), and [LangGraph releases](https://github.com/langchain-ai/langgraph/releases). The new integration uses stable APIs present in the repository lock; no dependency pin was silently raised as part of this feature.

Release in this order:

1. Run offline MCP, scope, and partial-backend tests.
2. Validate each source against a sanitized corpus; measure Recall@k, MRR/nDCG, citation precision, p95 latency, and model/tool-call counts.
3. Exercise partial outages, invalid book/run pairs, JWT denial, missing web scope, and Redis quota failures.
4. Start with a loopback local demo, then an internal JWT-protected canary with a small book scope.
5. Enable agentic Ask only after the evaluation gate passes. Keep hosted demo data synthetic/public and keep Settings web search disabled.
6. Add Distill cross-book evidence only after per-user corpus authorization and citation validation are implemented.
