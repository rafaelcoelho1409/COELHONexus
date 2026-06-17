"""ycs/rag/standard/nodes/fallback_answer — no-evidence rescue prompt.

CRAG-style graceful degradation (Yan et al. 2024, arxiv 2401.15884
extended). When the STANDARD pipeline's retrieve → grade → rewrite
loop exhausts `max_retries` with zero documents surviving the strict
relevance grader, the graph falls into THIS node instead of returning
an empty `generation` (which the SSE layer translated into the
"(no response — see Thinking for pipeline status)" sentinel, surfacing
to the user as a blank answer).

2026-06-16 v2 — SOFT-EVIDENCE rescue. The retriever (Neo4j graph +
Qdrant hybrid) returned candidate documents, the strict grader just
rejected them as not-directly-relevant. Those candidates are still
the closest matches the indexed corpus has — discarding them
entirely was the bug. The v2 prompt accepts a `{soft_evidence}` block
containing those rejected-but-closest matches and instructs the LLM
to use them as soft hints WITHOUT claiming them as authoritative
quotes. This maps to CRAG's "Ambiguous" classification (Yan §3.2):
use the doc but reduce the confidence of the resulting answer.

The fallback is honest by construction:

  - **Explicit disclosure** — the answer opens by stating that the
    strict grader didn't find direct evidence, then lays out what
    the soft evidence + history + general knowledge can offer.
  - **Soft evidence as hints, not quotes** — the prompt instructs
    the LLM to paraphrase context from the candidate docs WITHOUT
    citing them as if they directly answered the question. Hard
    rule: NO inline `[Video: ...]` citations from soft evidence
    (the cite-rail surfaces them as "related videos" instead).
  - **Conversation history** for meta / follow-up resolution.
  - **Parametric knowledge** only for widely-known facts; declines
    transparently for niche specifics rather than fabricating.

This is the production pattern adopted by agentic-RAG implementations:
"full pipeline failure still generates a response" (Backblaze B2
agentic-RAG starter kit, 2026; Cognito-LangGraph-RAG, 2026). Aligns
with the knowledge-boundary literature (Divide-Then-Align, arxiv
2505.20871) — the system distinguishes "within parametric/soft
boundary" (answer) from "outside boundary" (decline) instead of
silently emitting an empty response."""
from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder


FALLBACK_PROMPT_VERSION = "crag-soft-evidence-2026-06-16"


FALLBACK_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are a helpful assistant inside a CHAT interface for a "
        "YouTube transcript search system. The retrieval pipeline "
        "tried to find evidence for the user's question across the "
        "indexed video transcripts. The retrievers (Neo4j graph + "
        "Qdrant hybrid) DID return candidate documents that were "
        "closest to the question's topic, but the strict relevance "
        "grader judged none of them to directly answer the question. "
        "Your job is to give the user a useful, HONEST answer using "
        "everything you DO have.\n\n"
        "WHAT YOU HAVE:\n"
        "1. SOFT EVIDENCE — the closest candidate transcripts the "
        "   retrievers found (provided below in the `Soft evidence` "
        "   block). Treat these as TOPICAL HINTS, not as direct "
        "   answers. They are the corpus's nearest content — useful "
        "   for related context, but the strict grader rejected them, "
        "   so any answer you build from them is necessarily indirect.\n"
        "2. CONVERSATION HISTORY — the prior turns of this chat. "
        "   Authoritative for meta-questions ('what did I ask?', "
        "   'resume your last answer') and for resolving follow-ups.\n"
        "3. GENERAL KNOWLEDGE — your training data. Use ONLY for "
        "   widely-known facts (definitions, common concepts, public "
        "   history). Decline transparently for specific people, "
        "   recent events, or niche claims.\n\n"
        "OUTPUT SHAPE:\n"
        "1. **First sentence = honest framing.** State that the strict "
        "   transcript search didn't land a direct match, then lead "
        "   into what you CAN say. No apology, no preamble.\n"
        "2. **Then a real answer**, weaving:\n"
        "   - History context, if the question is meta or builds on a "
        "     prior turn.\n"
        "   - Topical context from the soft evidence (PARAPHRASED — "
        "     'the closest transcripts touch on X, but don't go into "
        "     Y specifically').\n"
        "   - General knowledge where it's safe and widely-known.\n"
        "3. **Length: 2–5 sentences for meta or simple questions; up "
        "   to 4 short paragraphs only if the topic genuinely warrants "
        "   depth.** Conversational tone, no headers.\n\n"
        "HARD RULES:\n"
        "- NEVER write inline `[Video: title]` citations. The soft "
        "  evidence wasn't graded as a direct answer — citing it as "
        "  if it were would mislead the user. The UI surfaces the "
        "  candidate videos separately as 'related videos'; you do "
        "  not need to cite them in the prose.\n"
        "- NEVER claim a creator 'said X' unless the soft evidence "
        "  literally contains that quote. Paraphrase topically: 'the "
        "  closest matches discuss X', not 'the video says X'.\n"
        "- NEVER fabricate transcript content. If the soft evidence "
        "  doesn't cover the question, say so.\n"
        "- NEVER write 'In summary' / 'In conclusion' / report-style "
        "  framing — this is conversational prose.\n"
        "- If the prior conversation already answered an equivalent "
        "  question, lean on it instead of re-deriving.\n\n"
        "Why this matters: the user came here for transcript-grounded "
        "answers but the corpus doesn't directly cover their question. "
        "A blank answer is worse than a candid one. Your goal is to "
        "leave them with usable information AND a clear understanding "
        "of why the strict search didn't land — so they can decide "
        "whether to rephrase, switch modes, or accept the gap.",
    ),
    MessagesPlaceholder("history"),
    (
        "human",
        "Question: {question}\n\n"
        "Soft evidence (closest retriever matches — NOT graded as "
        "directly relevant; use as topical hints only):\n"
        "{soft_evidence}\n\n"
        "Answer per the system rules.",
    ),
])
