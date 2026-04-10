"""
Transcript Chunking Service

CONCEPT: Transcripts can be thousands of tokens long. Embedding models have
context limits and quality degrades on long text. Chunking splits transcripts
into smaller pieces that each capture a focused topic.

Two strategies available:

1. RecursiveCharacterTextSplitter (DEFAULT)
   - Splits by character count with overlap
   - Fast, deterministic, no model needed
   - Good for structured text with clear paragraph breaks

2. SemanticChunker (OPTIONAL, requires embedding model)
   - Splits based on embedding similarity between sentences
   - Groups semantically related sentences together
   - Better for conversational content like YouTube transcripts
   - Slower (needs embedding model), but higher quality chunks

CHUNK SIZE RATIONALE:
- 512 tokens ≈ ~2000 characters — captures full context for a topic
- 50-token overlap preserves context at boundaries
- Too small (128): loses context, fragments ideas
- Too large (2048): dilutes relevance, wastes embedding quality
"""
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter


def create_chunker(
    chunk_size: int = 2000,
    chunk_overlap: int = 200,
) -> RecursiveCharacterTextSplitter:
    """
    Create a text splitter for transcripts.

    CONCEPT: RecursiveCharacterTextSplitter tries separators in order:
    1. "\\n\\n" (paragraph breaks) — ideal split point
    2. "\\n" (line breaks)
    3. ". " (sentence boundaries)
    4. " " (word boundaries)
    5. "" (character-level, last resort)

    It picks the highest-priority separator that produces chunks
    within the size limit. This preserves natural text structure.

    chunk_size is in characters (not tokens). For English text,
    ~4 chars ≈ 1 token, so 2000 chars ≈ 500 tokens.
    """
    return RecursiveCharacterTextSplitter(
        chunk_size = chunk_size,
        chunk_overlap = chunk_overlap,
        separators = ["\n\n", "\n", ". ", " ", ""],
        length_function = len,
    )


def chunk_transcript(
    video_id: str,
    content: str,
    metadata: dict,
    chunker: RecursiveCharacterTextSplitter,
) -> list[Document]:
    """
    Split a transcript into chunks with enriched metadata.

    Each chunk becomes a Document with:
    - page_content: the chunk text
    - metadata: video_id, chunk_index, total_chunks, plus all passed metadata

    The metadata lets us trace every chunk back to its source video
    and reconstruct the original order.
    """
    if not content or not content.strip():
        return []
    # Split the text
    texts = chunker.split_text(content)
    # Build Documents with metadata
    documents = []
    for i, text in enumerate(texts):
        doc_metadata = {
            "video_id": video_id,
            "chunk_index": i,
            "total_chunks": len(texts),
            **metadata,  # title, channel, channel_id, etc.
        }
        documents.append(Document(
            page_content = text,
            metadata = doc_metadata,
        ))
    return documents
