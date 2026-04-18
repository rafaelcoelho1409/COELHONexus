from langchain_core.prompts import ChatPromptTemplate


# =============================================================================
# Prompt Templates
# =============================================================================
GENERATE_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are a helpful assistant that answers questions about YouTube video content. "
        "Use ONLY the provided transcript excerpts to answer. "
        "Always cite your sources using this format: [Video: title] "
        "If the transcripts don't contain enough information, say so clearly.",
    ),
    (
        "human",
        "Question: {question}\n\n"
        "Video transcripts:\n{context}\n\n"
        "Answer the question based on the transcripts above. Include citations.",
    ),
])

REWRITE_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are a query rewriter. The original query did not return relevant results. "
        "Rewrite it to be more specific or use different terms that might match video transcripts. "
        "Return ONLY the rewritten query, nothing else.",
    ),
    (
        "human",
        "Original question: {question}\n"
        "Previous search query: {search_query}\n"
        "Rewrite this as a better search query:",
    ),
])

HALLUCINATION_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are a hallucination detector. Given an answer and the source documents it was "
        "generated from, determine:\n"
        "1. Is the answer GROUNDED in the documents? (no fabricated facts)\n"
        "2. Does the answer ADDRESS the original question?\n"
        "Be strict. If the answer contains ANY claim not supported by the documents, "
        "mark it as not grounded.",
    ),
    (
        "human",
        "Question: {question}\n\n"
        "Answer: {generation}\n\n"
        "Source documents:\n{documents}\n\n"
        "Evaluate the answer.",
    ),
])

CONTEXTUALIZE_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are a question contextualizer. Given a conversation history and a new question, "
        "determine if the question references previous context (pronouns like 'she', 'he', "
        "'that', 'it', 'they', phrases like 'tell me more', 'what about', 'the same', "
        "'and what about', or any implicit references to prior topics).\n\n"
        "If YES: Rewrite the question as a standalone question that includes the necessary context.\n"
        "If NO: Return the original question unchanged.\n\n"
        "Return ONLY the rewritten question. Nothing else.",
    ),
    (
        "human",
        "Conversation history:\n{history}\n\nNew question: {question}",
    ),
])

CLASSIFY_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are a query complexity classifier for a YouTube transcript search system. "
        "Classify the user's question into one of three modes:\n\n"
        "FAST — Simple factual questions answerable from general knowledge. "
        "Examples: 'What is citizenship by investment?', 'What does CBI stand for?'\n\n"
        "STANDARD — Questions that need evidence from video transcripts. "
        "Examples: 'What does Wealthy Expat say about Dubai?', "
        "'Compare Dominica vs Grenada for citizenship', "
        "'What are the tax benefits of living in Dubai?'\n\n"
        "DEEP — Analytical questions requiring multi-faceted analysis across many videos. "
        "Pattern-finding, psychological analysis, contradiction detection, hidden assumptions. "
        "Examples: 'What psychological traits does this creator show?', "
        "'What contradictions exist across all videos?', "
        "'What hidden assumptions does this channel never question?'\n\n"
        "When uncertain, default to STANDARD.\n"
        "For DEEP mode, also generate 3-8 focused sub-questions that break down the analysis.\n\n"
        "SCOPE DETECTION: Identify any specific channel or person names mentioned in the query. "
        "Return them in channel_names so retrieval can be scoped to their content only.\n"
        "Examples:\n"
        "- 'What does Vitoria Stecca think about X?' → channel_names: ['Vitoria Stecca']\n"
        "- 'Compare Rafael Cintron and Vitoria Stecca' → channel_names: ['Rafael Cintron', 'Vitoria Stecca']\n"
        "- 'What are the best tax strategies?' → channel_names: [] (no specific person/channel)\n"
        "If the query is about a SPECIFIC person/channel, always include their name.",
    ),
    ("human", "{question}"),
])

DIRECT_ANSWER_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are a helpful assistant. Answer the user's question concisely from your "
        "general knowledge. If you are uncertain or the question requires specific "
        "video transcript evidence, say so clearly.",
    ),
    ("human", "{question}"),
])

SYNTHESIZE_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are a research synthesizer. You receive the results of multiple parallel "
        "research sub-questions about the same overarching topic. Your job is to:\n"
        "1. Combine all findings into a coherent analytical report\n"
        "2. Cross-reference findings — identify patterns that emerge across sub-questions\n"
        "3. Note any contradictions or tensions between findings\n"
        "4. Structure the output clearly with sections\n"
        "5. Cite sources using [Video: title] format\n"
        "Do NOT fabricate information. Only synthesize what the sub-research found.",
    ),
    (
        "human",
        "Original question: {question}\n\n"
        "Research plan: {research_plan}\n\n"
        "Sub-research findings:\n{sub_results}\n\n"
        "Synthesize these findings into a comprehensive analytical report.",
    ),
])

CRITIC_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are a research quality critic. Evaluate the synthesis against the "
        "sub-research findings. Check:\n"
        "1. Is every claim in the synthesis supported by at least one sub-research finding?\n"
        "2. Are there contradictions within the synthesis itself?\n"
        "3. Did the synthesis adequately cover all sub-questions?\n"
        "4. Assign a confidence score from 0.0 (unreliable) to 1.0 (fully supported).\n"
        "Be strict but fair.",
    ),
    (
        "human",
        "Original question: {question}\n\n"
        "Synthesis:\n{synthesis}\n\n"
        "Sub-research findings:\n{sub_results}\n\n"
        "Evaluate the synthesis.",
    ),
])
