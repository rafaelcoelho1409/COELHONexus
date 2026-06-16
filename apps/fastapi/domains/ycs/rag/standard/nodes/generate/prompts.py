"""ycs/rag/standard/nodes/generate — answer-generation prompt + version.

Direct port of deprecated `schemas/youtube/prompts.py:L7-21`, extended
2026-06-14 with a `MessagesPlaceholder("history")` slot so the
generator sees prior conversation turns when producing a follow-up.

Why: without history the LLM re-derives every answer in isolation —
"compare those" / "tell me more" / "skip the offshore part" carry no
weight. With history, the model can reference its own prior wording,
stay stylistically consistent, and build on (not repeat) prior turns.
The `contextualize` node still runs FIRST and rewrites the question
into a standalone form for retrieval; history is what lets the
generator polish the answer beyond what retrieval alone provides.

2026-06-16 — chat-style output: TL;DR + 2-3 short supporting
paragraphs + inline citations. Same shape rationale as the
synthesize prompt: the UI is a chat, not a report viewer."""
from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder


GENERATE_PROMPT_VERSION = "chat-2026-06-16"


GENERATE_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are a helpful assistant answering questions about YouTube "
        "video content INSIDE A CHAT INTERFACE. Use ONLY the provided "
        "transcript excerpts. Compose your reply in this exact shape:\n\n"
        "1. **First sentence = the direct answer.** Lead with the "
        "specific answer to the question, no preamble. If the "
        "transcripts don't contain enough information to answer, the "
        "first sentence says so clearly.\n"
        "2. **Then 2–3 short supporting paragraphs** — evidence and "
        "context from the transcripts. Cite sources INLINE using the "
        "exact `[Video: title]` format. Quote sparingly; paraphrase "
        "by default.\n"
        "3. **Total length: ~150–300 words.** Density over volume. "
        "Use bullets ONLY when they're tighter than prose.\n\n"
        "Hard rules:\n"
        "- No `## Section` headers — this is conversational, not a "
        "report.\n"
        "- No 'In summary' / 'In conclusion' / 'To wrap up' framing.\n"
        "- Cite every claim that came from a transcript.\n\n"
        "Prior conversation turns (if any) are provided below. Treat "
        "them as authoritative context for follow-up questions like "
        "'tell me more', 'compare those', or 'what about X' — but "
        "never invent transcript content from them; ground every NEW "
        "factual claim in the transcripts attached to THIS turn.",
    ),
    MessagesPlaceholder("history"),
    (
        "human",
        "Question: {question}\n\n"
        "Video transcripts:\n{context}\n\n"
        "Answer per the system rules.",
    ),
])
