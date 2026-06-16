"""ycs/rag/adaptive/nodes/subagent — LLM prompts for the sub-agent.

The sub-agent itself delegates its work to the STANDARD sub-graph;
this module only carries prompts for the sub-agent's OWN logic, i.e.
the failure-recovery rephrase added 2026-06-16 (see `node.py::
_rephrase_subquestion`).

Versioned per `docs/CODE-CONVENTIONS.md` §2: cache-invalidation knobs
live with the prompts they identify."""
from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate


REPHRASE_PROMPT_VERSION = "no-docs-retry-2026-06-16"


# Reframe a failed sub-question so the STANDARD sub-graph's retrieval
# + grading have a second chance at finding evidence. The first
# attempt typically failed because the literal phrasing didn't match
# the corpus' vocabulary — `recurring emotional tones?` returns no
# direct hits against a corpus that uses words like `frustration`,
# `urgency`, or `concern`. The rephrase aims to:
#   - widen the lexical surface (synonyms, near-synonyms)
#   - shift from abstract to concrete framing
#   - preserve the original intent
REPHRASE_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You rewrite a research sub-question that failed to retrieve "
        "any relevant transcript evidence. Your rewrite should keep "
        "the SAME underlying intent but use different surface "
        "vocabulary that's more likely to match the words speakers "
        "actually use on video.\n\n"
        "Concretely:\n"
        "- Replace abstract framings ('emotional tones', 'thematic "
        "evolution') with concrete vocabulary ('frustration / hope / "
        "fear', 'how speakers describe X over time').\n"
        "- Widen the question if it was too narrow, narrow it if it "
        "was too broad.\n"
        "- Keep it ONE sentence, terminating in '?'.\n\n"
        "Output ONLY the rewritten question. No preface, no quotes, "
        "no explanation.",
    ),
    (
        "human",
        "Original sub-question: {sub_question}\n\n"
        "Original parent question (for intent context): {parent_question}\n\n"
        "Rewrite the sub-question.",
    ),
])
