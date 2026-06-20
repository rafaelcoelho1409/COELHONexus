"""ycs/rag/adaptive/nodes/classify — query-complexity + scope-detect prompt.

Direct port of deprecated `schemas/youtube/prompts.py:L74-101`. Single
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
        "system. This tool exists to answer questions WITH EVIDENCE "
        "from indexed video transcripts. Choose STANDARD by default. "
        "Questions arrive in MANY LANGUAGES (Portuguese, Spanish, "
        "English, etc.) — classify by INTENT, not language.\n\n"
        "RULES (apply in order):\n"
        "1. Any question containing a question word (how / why / what / "
        "   who / where / when / which / how-much / how-many / qual / "
        "   quais / quem / como / por que / quando / onde / quanto) "
        "   → STANDARD or DEEP. Never FAST.\n"
        "2. Any question about benefits, requirements, examples, "
        "   companies, people, opinions, recommendations, comparisons, "
        "   experiences, claims, or specific facts → STANDARD.\n"
        "3. Any cross-corpus analytical question (patterns, "
        "   contradictions, psychological traits, hidden assumptions, "
        "   narrative arcs across many videos) → DEEP.\n"
        "4. FAST is reserved ONLY for plain acronym / dictionary "
        "   definitions where the answer cannot vary by source. "
        "   Examples: 'What does CBI stand for?', 'O que significa a "
        "   sigla XYZ?'. If you cannot prove the question is a pure "
        "   acronym/definition lookup, do NOT use FAST.\n\n"
        "Why this matters: FAST bypasses retrieval entirely and the "
        "model fabricates an answer from training data. That is a "
        "FAILURE mode for this product — users came here for "
        "transcript-grounded answers, not generic LLM hallucinations. "
        "When in doubt, always pick STANDARD. Picking FAST when "
        "STANDARD was correct is FAR worse than the inverse.\n\n"
        "Examples — most questions should land in STANDARD:\n"
        "- 'What does Wealthy Expat say about Dubai?' → STANDARD\n"
        "- 'Compare Dominica vs Grenada for citizenship' → STANDARD\n"
        "- 'Como funciona a Lei de Maquila no Paraguai?' → STANDARD\n"
        "- 'Quais empresas se beneficiam da Lei X?' → STANDARD\n"
        "- 'Quais os benefícios das contas offshore?' → STANDARD\n"
        "- 'What are the tax benefits of living in Dubai?' → STANDARD\n"
        "- 'O que esse canal diz sobre Y?' → STANDARD\n"
        "- 'Como abrir uma conta offshore?' → STANDARD\n"
        "- 'What does CBI stand for?' → FAST (acronym only)\n"
        "- 'O que significa offshore?' → FAST (pure definition)\n"
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
        "Return ONLY a JSON object with keys: mode, reasoning, "
        "sub_questions, channel_names. Do not wrap it in markdown.",
    ),
    ("human", "{question}"),
])
