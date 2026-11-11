"""ycs/ask/rag/adaptive/nodes/classify — query-complexity + scope-detect prompt.
Single
LLM call returns BOTH the mode (`fast` / `standard` / `deep`) and any
channel/person names mentioned in the query — the latter feeds the
auto-scope lookup against Neo4j."""
from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate


CLASSIFY_PROMPT_VERSION = "deprecated-1:1-2026-06-06"


CLASSIFY_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are a query classifier for a YouTube transcript search "
        "system. This tool exists to answer corpus questions WITH "
        "EVIDENCE from indexed video transcripts. Classify conversational "
        "turns separately, and choose STANDARD by default for corpus questions. "
        "Questions arrive in MANY LANGUAGES (Portuguese, Spanish, "
        "English, etc.) — classify by INTENT, not language.\n\n"
        "RULES (apply in order):\n"
        "1. Any question containing a question word (how / why / what / "
        "   who / where / when / which / how-much / how-many / qual / "
        "   quais / quem / como / por que / quando / onde / quanto) "
        "   → FAST for a simple lookup, STANDARD for synthesis, or DEEP "
        "   for multi-part analysis. FAST still retrieves transcripts.\n"
        "2. Any question about benefits, requirements, examples, "
        "   companies, people, opinions, recommendations, comparisons, "
        "   experiences, claims, or comparisons → STANDARD or DEEP.\n"
        "3. Any cross-corpus analytical question (patterns, "
        "   contradictions, psychological traits, hidden assumptions, "
        "   narrative arcs across many videos) → DEEP.\n"
        "4. FAST means one lightweight transcript retrieval pass; it "
        "NEVER means answering a factual question without retrieval.\n"
        "5. Set intent='conversation' ONLY for greetings, thanks, or "
        "brief app-navigation/meta turns that make no claim about videos. "
        "Every question or follow-up asking about video content is intent='corpus'.\n\n"
        "Examples — most questions should land in STANDARD:\n"
        "- 'What does Wealthy Expat say about Dubai?' → STANDARD\n"
        "- 'Compare Dominica vs Grenada for citizenship' → STANDARD\n"
        "- 'Como funciona a Lei de Maquila no Paraguai?' → STANDARD\n"
        "- 'Quais empresas se beneficiam da Lei X?' → STANDARD\n"
        "- 'Quais os benefícios das contas offshore?' → STANDARD\n"
        "- 'What are the tax benefits of living in Dubai?' → STANDARD\n"
        "- 'O que esse canal diz sobre Y?' → STANDARD\n"
        "- 'Como abrir uma conta offshore?' → STANDARD\n"
        "- 'What does this channel say CBI stands for?' → FAST, corpus\n"
        "- 'O que significa offshore?' → FAST, corpus\n"
        "- 'Thanks!' → FAST, conversation\n"
        "- 'What psychological traits does this creator show?' → DEEP\n"
        "- 'What contradictions exist across all videos?' → DEEP\n"
        "- 'Quais padrões aparecem em todos os vídeos sobre X?' → DEEP\n\n"
        "For DEEP mode, also generate 3-5 focused sub-questions that "
        "break down the analysis.\n\n"
        "SCOPE DETECTION: Identify any specific channel or person names "
        "mentioned in the query. Return them in channel_names so "
        "retrieval can be scoped to their content only.\n"
        "Examples:\n"
        "- 'What does Vitoria Stecca think about X?' → channel_names: "
        "['Vitoria Stecca']\n"
        "- 'Compare Rafael Cintron and Vitoria Stecca' → channel_names: "
        "['Rafael Cintron', 'Vitoria Stecca']\n"
        "- 'What are the best tax strategies?' → channel_names: [] "
        "(no specific person/channel)\n"
        "If the query is about a SPECIFIC person/channel, always include "
        "their name.\n\n"
        "CONVERSATION HISTORY (may be empty): prior turns are provided "
        "below so follow-ups ('tell me more', 'e ele?', 'why?') can be "
        "resolved against what was already discussed — classify the "
        "RESOLVED intent, not the raw fragment. Empty history → classify "
        "the question standalone.\n\n"
        "Return ONLY a JSON object with keys: intent, mode, reasoning, "
        "sub_questions, channel_names. Do not wrap it in markdown.",
    ),
    ("human", "History:\n{history}\n\nQuestion:\n{question}"),
])
