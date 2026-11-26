# BD Agentic RAG and FastMCP rollout

## Architecture

`/mcp` remains one shared FastMCP endpoint. The BD component registers three capability groups:

- `bd_search_elasticsearch` for bounded BM25 passage retrieval.
- `bd_search_qdrant` for dense and sparse hybrid retrieval.
- `bd_search_neo4j` for fixed read-only entity and relationship retrieval.
- `bd_search_web` for the Settings-selected external Search MCP provider.

The three internal-store tools use the same current-index pointer and exact run and processed-book revision filters already used by BD Ask. They return evidence IDs, citations, and an explicit per-source status. The graph and MCP tools never accept raw Elasticsearch DSL, Cypher, arbitrary Qdrant filters, or arbitrary URLs.

The same BD component exposes lifecycle operations for the existing Agentic Ingestion and processed-book indexing workflows. The tools are `bd_ingestion_get_run`, `bd_ingestion_get_quality_summary`, `bd_ingestion_start_run`, `bd_ingestion_resume_run`, `bd_ingestion_cancel_run`, `bd_ingestion_replay_from_node`, and the corresponding `bd_rag_index_*` read, start, resume, cancel, and replay operations. Mutations go through domain services and the existing Celery workers; MCP does not create a second execution engine. Ingestion start requires a client idempotency key. Replay accepts only graph node IDs and requires explicit confirmation.

Resource templates expose compact, projected run and quality snapshots at `bd://books/{book_id}/ingestion/...` and `bd://books/{book_id}/rag/...`. The prompts `bd_review_ingestion_run` and `bd_diagnose_rag_index_run` guide read-only investigation and do not execute lifecycle tools. Resource and tool reads omit page text, object keys, and backend collection/index names. Current-index reads validate the publication pointer against the run attempt and processed-book revision.

Distill's book-retrieval subgraph fans out to four Distill-owned DeepAgents, each with only its source's MCP adapter tool. Ask has a separate Ask-owned subgraph for Elasticsearch, Qdrant, and Neo4j; Ask's parent graph handles optional external search. Each workflow owns its agent and prompt packages, and each fuses evidence deterministically with reciprocal rank fusion. A specialist's prose is never treated as evidence. The Search MCP lane is optional because it uses an external provider and may incur cost.

The processed-book indexing workflow launched from BD Ingestion lives under `ai/bd/graphs/ingestion/rag_indexing`. Ask's query-time retrieval lives under `ai/bd/graphs/ask/adaptive/subgraphs/book_retrieval`; Distill's retrieval lives under `ai/bd/graphs/distill/book_distillation/subgraphs/book_retrieval`.

## Deployment profiles

`BD_MCP_PROFILE` defaults to `disabled`. In that profile, `/mcp` exposes only `ping`.

For a local demonstration, set `BD_MCP_PROFILE=local_demo` and `BD_MCP_DEMO_BOOK_IDS` to a comma-separated list containing only public or synthetic demo books. The app rejects this profile in production and the MCP ASGI app accepts loopback clients only. It permits reads for those books and denies every mutation. Settings-backed Search MCP remains disabled.

For a protected deployment, set `BD_MCP_PROFILE=protected` and configure:

```text
BD_MCP_JWKS_URI=https://identity.example/.well-known/jwks.json
BD_MCP_ISSUER=https://identity.example/
BD_MCP_AUDIENCE=coelho-nexus-mcp
```

The JWT must contain `bd_book_ids: ["..."]` (or `bd: {"book_ids": ["..."]}`); each requested book must be listed. FastMCP validates the JWT signature, issuer, and audience. Each capability then checks its own scope and the requested book claim. `BD_MCP_REQUIRED_SCOPE` can optionally impose an additional endpoint-wide baseline scope; leave it unset when callers should receive only the scope for the capabilities they use. A gateway must preserve the verified bearer token and prevent direct access around the protected endpoint.

The operation scope settings default to `bd.search.read`, `bd.ingestion.read`, `bd.ingestion.write`, `bd.ingestion.replay`, `bd.rag.index.read`, `bd.rag.index.write`, and `bd.rag.index.replay`, respectively. They can be changed with `BD_MCP_SEARCH_READ_SCOPE`, `BD_MCP_INGESTION_READ_SCOPE`, `BD_MCP_INGESTION_WRITE_SCOPE`, `BD_MCP_INGESTION_REPLAY_SCOPE`, `BD_MCP_RAG_INDEX_READ_SCOPE`, `BD_MCP_RAG_INDEX_WRITE_SCOPE`, and `BD_MCP_RAG_INDEX_REPLAY_SCOPE`. The protected deployment should grant replay scopes only to operators allowed to regenerate model-derived or indexed outputs.

Settings-backed web search is off by default. Enabling it requires `BD_MCP_WEB_SEARCH_ENABLED=true`, both `bd.search.read` and the additional token scope `bd.search.web`, and Redis for a per-principal request budget of 10 searches per minute. This path uses the existing selected Settings connection; never enable it for a public demo that has access to private Settings credentials.

## Agent rollout

`BD_RAG_SPECIALIST_AGENTS_ENABLED` defaults to `false`. Set it to `true` in a local or internal canary to route BD Ask through the four-specialist graph. The current deterministic Ask retriever remains the default until an evaluation corpus shows improved recall and citation quality at an acceptable latency and model-call cost.

The graph can include Search MCP by passing `include_web=true`; callers must explicitly choose this because web evidence is external and may use a configured provider. BD Ask's existing external-search stage remains in place during the canary.

Distill's current Search MCP stage stays separate. It validates quotations against full source text, while the new web MCP tool returns bounded search snippets. Distill should receive cross-book retrieval only after the product has a tenant/corpus authorization contract and citation schema for internal RAG evidence. Searching the current book again would duplicate the source already used to build the guide and would not satisfy related-corpus retrieval.

BD Query's raw database workbench remains separate from the safe evidence tools. Query and other MCP clients can call the shared retrieval tools directly; raw query execution is not exposed through the RAG MCP surface.

## Compatibility and release sequence

The server dependency is on the FastMCP 4 line (`>=4.0.11,<5.0`; lock resolves 4.1.3). The package's seven-day cooldown remains in force, so this feature uses the committed lock and does not move the dependency cutoff. Upgrade FastMCP separately after the cooldown and migration review. DeepAgents and LangGraph upgrades also remain separate dependency changes.

The integration uses FastMCP APIs present in the repository lock; no dependency pin was silently raised as part of this feature. FastMCP Tasks, subscriptions/notifications, and Apps remain deferred. Existing Celery/LangGraph execution remains the owner of long-running ingestion and indexing jobs.

Release in this order:

1. Run offline MCP, scope, and partial-backend tests.
2. Validate each source against a sanitized corpus; measure Recall@k, MRR/nDCG, citation precision, p95 latency, and model/tool-call counts.
3. Exercise partial outages, invalid book/run pairs, JWT denial, missing web scope, and Redis quota failures.
4. Start with a loopback local demo, then an internal JWT-protected canary with a small book scope.
5. Enable agentic Ask only after the evaluation gate passes. Keep hosted demo data synthetic/public and keep Settings web search disabled.
6. Add Distill cross-book evidence only after per-user corpus authorization and citation validation are implemented.
