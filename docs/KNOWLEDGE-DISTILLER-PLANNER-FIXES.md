# Knowledge Distiller — Planner Fixes (post-run 2026-04-21)

Research-backed fixes for 4 planner failures observed during the `c5de2c9d-dc3c-4e71-94cc-006adea02db7` run on a 994-file DeepAgents corpus.

Touches: `apps/fastapi/graphs/knowledge/distiller.py`, `apps/fastapi/agents/knowledge.py` (planner node), `apps/fastapi/services/llm_chain.py`, `apps/fastapi/schemas/knowledge/prompts.py`.

## Single highest-leverage fix

**Stop sending 1.87 MB into one LLM call. Refactor the planner to map-reduce using LangGraph's `Send()` API.** This one change dissolves 3 of the 4 failures simultaneously (413, 504, truncation).

## Failure #1 — HTTP 413 (Groq) / 504 (NIM)

### Root cause (not context window — it's TPM limits)

`llama-3.3-70b-versatile` advertises 128K context, but Groq's `on_demand` (free) tier caps **TPM at 12,000** for that model. Our 1.87 MB prompt ≈ **~470K tokens** — well over the per-minute budget. Groq rejects with 413 **before the model ever runs**.

NIM 504 is the same class of problem — prompt stalls the gateway's proxy timeout.

**Evidence:**
- [continuedev/continue#10218](https://github.com/continuedev/continue/issues/10218): `413 Request too large for model llama-3.3-70b-versatile ... service tier on_demand on tokens per minute (TPM): Limit 12000, Requested 14137`
- [rsynnot/magic-lists-for-navidrome#29](https://github.com/rsynnot/magic-lists-for-navidrome/issues/29)

### Fix: Two-pass map-reduce planner via LangGraph `Send()`

1. **Cluster pass** — send only file metadata per file: `(slug, title, first 200 chars)`, batched into shards of ≤40 files. Each shard LLM call is well under 12K TPM. Each returns a topic label + a tentative file grouping.
2. **Chapter pass** — a reducer that merges shards sharing labels into 4-12 chapters.

Use LangGraph `Send()` to fan out shards in parallel.

**Reference patterns:**
- [LangChain Academy §7.1 — Map-Reduce](https://deepwiki.com/langchain-ai/langchain-academy/7.1-map-reduce-pattern) (canonical pattern)
- [Map-Reduce with Send API in LangGraph](https://medium.com/ai-engineering-bootcamp/map-reduce-with-the-send-api-in-langgraph-29b92078b47d) (worked example)

**Confidence:** HIGH. **Impact:** unblocks both endpoints.

## Failure #2 — Output truncation (761/994 files missing)

### Root cause

`max_output_tokens` cap. A list of ~1000 slugs in JSON costs ~8-15K output tokens; Groq's default completion cap on this model is 8K; on `on_demand` tier, effective budget is even tighter. Function-calling response gets cut mid-array, Pydantic sees a valid-but-incomplete object, planner accepts it silently.

**Evidence:** [langchain-ai/langchain#35320](https://github.com/langchain-ai/langchain/issues/35320) — documents the silent-truncation trap with structured output.

### Fix (3 layers)

1. **Primary** — the map-reduce refactor (Fix #1 above). Each worker returns a small slug list, nothing ever overflows.
2. **Detection** — use `with_structured_output(..., include_raw=True)` and inspect `raw.response_metadata["finish_reason"]`; raise+retry if `"length"`:
   ```python
   chain = llm.with_structured_output(ChapterPlanShard, method="function_calling", include_raw=True)
   resp = await chain.ainvoke(prompt)
   if resp["raw"].response_metadata.get("finish_reason") == "length":
       raise OutputTruncatedError(...)  # trigger retry with smaller shard
   plan = resp["parsed"]
   ```
3. **Explicit cap** — set `max_tokens=8192` on the ChatGroq constructor + enforce a per-worker slug budget (~30 slugs/worker).

**Confidence:** HIGH. **Impact:** 100% corpus coverage guaranteed; no silent drops.

## Failure #3 — Hallucinated slugs (21 invented file names)

### Root cause

`with_structured_output(..., method="function_calling")` does **NOT** enforce enum constraints at decode time on Groq. Function-calling mode is advisory-only for enum. The LLM freely invents tokens. NIM is similar.

### Fix (layered — constrain at decode time)

1. **Groq JSON-Schema mode (strict)** — use `response_format={"type":"json_schema", "json_schema":{...}, "strict": true}` instead of function-calling mode. Emit each worker's `assigned_files` as `{"type":"array","items":{"type":"string","enum":[...shard_slugs...]}}`. Enum size stays small (~40 slugs per worker).
   ```python
   schema = {
       "type": "object",
       "properties": {
           "chapter_title": {"type": "string"},
           "assigned_files": {
               "type": "array",
               "items": {"type": "string", "enum": shard_slugs},
           },
       },
       "required": ["chapter_title", "assigned_files"],
   }
   chain = llm.bind(response_format={"type": "json_schema", "json_schema": {"schema": schema, "strict": True}})
   ```
   Reference: [Groq structured-outputs docs](https://console.groq.com/docs/structured-outputs).

2. **NIM / OpenAI path** — same mechanism. OpenAI's strict-schema enum cap is **1,000 values** ([community.openai.com/t/1313593](https://community.openai.com/t/structured-outputs-limits-are-raised-to-support-larger-schemas/1313593)). Since each shard is ~40 slugs, well under the cap.

3. **Post-validation net** — keep the existing set-membership check as defense-in-depth. Reject + retry on any miss.

4. **Experimental fallback** — if hosted constrained-decoding misbehaves: [Outlines library](https://www.marktechpost.com/2026/03/14/how-to-build-type-safe-schema-constrained-and-function-driven-llm-pipelines-using-outlines-and-pydantic/) for grammar-constrained decoding (requires local model).

**Confidence:** HIGH for strict JSON-schema mode; MEDIUM on function-calling enum enforcement.
**Impact:** hallucination rate → ~0.

## Failure #4 — 77% corpus excluded (thin downstream context)

### Root cause

Symptom of #2 (truncation) + single-prompt planning bias. One LLM, overwhelmed, emits a small "safe" assignment. Production pattern is **cluster-then-label**, exactly what GraphRAG / DSPy-style pipelines do.

**Evidence:**
- [f22labs — map-reduce for summarization](https://www.f22labs.com/blogs/map-reduce-for-large-document-summarization-with-llms/)
- [SPD-RAG arxiv 2603.08329](https://arxiv.org/html/2603.08329v1)

### Fix

After map-reduce is in place, add a **reducer node invariant**:
```python
assert assigned_files | unused_files == all_slugs, "planner missed some files"
```

Force reassignment of orphans to the nearest chapter by embedding similarity (reuse existing vectorstore if available, else simple TF-IDF cosine).

**Confidence:** HIGH. **Impact:** coverage becomes guaranteed, not hoped for.

## Ranked implementation order

1. **Refactor planner to map-reduce via `Send()`** — shards ≤40 files, label-then-cluster. Solves #1, #2, #4.
2. **Switch to strict JSON-Schema mode with per-shard enum** — solves #3.
3. **Add `include_raw=True` + `finish_reason` guard** — defense in depth against #2.
4. **Coverage invariant in reducer node** — guarantees #4.
5. Keep `llama-3.3-70b-versatile` but each shard easily fits under 12K TPM.

Do #1 first. Everything else becomes cheap once the prompt shape is right.

## What NOT to try

- Upgrading to Groq's paid tier — the 1.87 MB single-prompt design is wrong regardless of TPM budget; fix the architecture, not the wallet.
- Switching to a smaller model — would be even more prone to hallucinations + truncation at this scale.
- Pure LangChain RetrievalQA over the corpus — wrong tool; we need organizational decomposition, not Q&A.
- Hand-rolled JSON parsing with regex repair — brittle; strict JSON-schema mode gets us this for free.

## Sources

- https://github.com/continuedev/continue/issues/10218 (Groq TPM 413)
- https://github.com/langchain-ai/langchain/issues/35320 (silent truncation)
- https://console.groq.com/docs/structured-outputs (Groq strict schema)
- https://community.openai.com/t/structured-outputs-limits-are-raised-to-support-larger-schemas/1313593 (enum cap)
- https://deepwiki.com/langchain-ai/langchain-academy/7.1-map-reduce-pattern
- https://medium.com/ai-engineering-bootcamp/map-reduce-with-the-send-api-in-langgraph-29b92078b47d
- https://www.f22labs.com/blogs/map-reduce-for-large-document-summarization-with-llms/
- https://arxiv.org/html/2603.08329v1 (SPD-RAG)
- https://www.marktechpost.com/2026/03/14/how-to-build-type-safe-schema-constrained-and-function-driven-llm-pipelines-using-outlines-and-pydantic/ (Outlines)
