# Elasticsearch storage for BD and YCS RAG

## Index layout

Elasticsearch stores canonical source records and a lexical passage projection.
It does not store dense or sparse vectors; Qdrant owns those vectors, and Neo4j
owns the knowledge graph.

| Feature | Stable alias | Physical index | Document purpose |
| --- | --- | --- | --- |
| YCS metadata | `coelhonexus-ycs-metadata` | `coelhonexus-ycs-metadata-v1` | Canonical video metadata |
| YCS transcripts | `coelhonexus-ycs-transcriptions` | `coelhonexus-ycs-transcriptions-v1` | Canonical transcript per video/language or partition |
| YCS BM25 passages | `coelhonexus-ycs-rag-bm25-passages` | `coelhonexus-ycs-rag-bm25-passages-v1` | BM25-searchable transcript passages with transcript and character-offset provenance |
| BD processed pages | `coelhonexus-bd-ingestion` | `coelhonexus-bd-ingestion-v1` | Canonical processed page shown by BD Pages and consumed by Distill |
| BD BM25 passages | `coelhonexus-bd-rag-bm25-passages` | `coelhonexus-bd-rag-bm25-passages-v1` | BM25-searchable page passages with book, revision, section, and character-offset provenance |

Application code reads and writes through stable aliases. Versioned physical
names allow a future mapping change to create a new index and move the alias
after validation instead of changing a populated mapping in place. Startup
creates missing physical indexes and aliases; it does not recreate deleted
documents.

## Writer and retrieval behavior

- YCS transcript ingestion writes the canonical transcript and derived passage
  documents in one bulk request with `refresh="wait_for"`. Passage text uses
  the shared YCS recursive chunker and records the chunking profile and source
  character offsets. Updating a transcript removes its stale passage projection
  before writing the new one. YCS Ask uses the passage alias for Elasticsearch
  BM25, Qdrant for dense/sparse vector retrieval, and Neo4j for graph evidence.
  The passage projection uses the shared default chunk profile; a caller that
  explicitly overrides chunk size for a separate Qdrant-only reindex can create
  different vector chunk boundaries, while both backends retain transcript
  offsets for source grounding.
- BD Agentic RAG writes one canonical processed-page document and the page's
  derived passage documents. Elasticsearch and Qdrant share the BD passage
  chunker and profile, so passage counts are checked before the retrieval index
  is published. BD Ask uses the passage alias for BM25, Qdrant for dense/sparse
  retrieval, and Neo4j for graph evidence.
- Passage documents preserve enough source identity to cite a YCS video and
  transcript segment or a BD book, processed page, and section. Canonical
  records remain available for full-record browsing and source reconstruction.

## Rebuilding after deleting indices

The server will recreate the five empty physical indexes and aliases at
startup. It cannot recover documents from those indexes:

- Re-run YCS extraction for the relevant videos/channels/playlists to restore
  metadata, transcripts, and BM25 passages. The existing Qdrant and Neo4j data
  are separate stores.
- Re-run BD Agentic RAG indexing for each processed book to republish canonical
  processed pages and BM25 passages. The original ingestion and processed-book
  files remain in their existing storage; Elasticsearch deletion does not
  remove them.

For future mapping changes, create a new physical version (for example `-v2`),
validate its document count and retrieval behavior, then atomically move the
stable alias. Do not point an alias at two versions during normal writes.
