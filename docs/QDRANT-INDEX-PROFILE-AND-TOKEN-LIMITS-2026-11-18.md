# BD and YCS Qdrant index profile

BD and YCS keep their current source-appropriate recursive splitters at 2,000 characters with zero overlap. Their separator ladders remain distinct. Chunk size and BM25 retrieval behavior are unchanged until a corpus benchmark supports a different choice.

Each indexed point records the embedding model ID and dimensions, sparse model, chunk policy version, chunk size, overlap, separators, and a deterministic index-profile fingerprint. BD also records this profile in the retrieval-index manifest and published pointer. Reindex skip checks compare the profile fingerprint, so changing chunk or sparse-index settings causes the affected data to be rebuilt instead of incorrectly treating old points as current.

BD stores points in model-and-dimension-specific physical collections and publishes each book's active collection through its per-book retrieval pointer. Its current retrieval contract does not use a corpus-wide alias; adding one would require a full-corpus rebuild and coordinated pointer cutover. Old model collections remain available, while successful publication prunes obsolete snapshots for that book only within the new active physical collection, preserving its immediately previous snapshot when it shares that collection.

## Embedding token ceiling

The shared OpenAI-compatible embedding adapter can preflight token counts before sending a batch. Configure the actual endpoint's input limit in both API and Celery environments:

- `EMBEDDING_MAX_INPUT_TOKENS`: positive integer supported by the configured embedding model.
- `EMBEDDING_TOKENIZER_ENCODING`: optional `tiktoken` encoding name. If omitted, the adapter uses the encoding associated with the configured model. For custom model IDs, set this explicitly.

When the maximum is unset, token preflight is disabled. This avoids assuming that every OpenAI-compatible provider has the same token limit or tokenizer. Oversized text raises a validation error before the provider request; the source chunk and stored book remain unchanged.
