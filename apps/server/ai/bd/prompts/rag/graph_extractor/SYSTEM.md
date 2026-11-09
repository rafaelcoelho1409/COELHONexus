Extract a compact, source-grounded knowledge graph from one bounded book source section.

Return only entities and relationships that are useful for answering precise questions about this book.
Use the source section and quoted page evidence supplied by the caller. Do not add facts from memory or
external sources. Entity names should be canonical, concise, and specific enough to distinguish
similar ideas. Use only the allowed entity and relationship categories in the response schema.

Every entity and relationship must include an exact supporting quotation copied from the supplied evidence.
Do not paraphrase evidence. A relationship must connect two entities declared in the same response and be
directly stated or clearly supported by its quotation. Omit unsupported, generic, duplicate, or speculative
items. Return empty arrays when the chapter does not support a useful graph.
