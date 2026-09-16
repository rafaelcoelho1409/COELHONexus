"""ycs/rag/standard/nodes/corroborate — corroboration-judge prompt.

2026-09-16 — closes the gap CRAG (Yan et al. 2024) calls "Ambiguous"
handling: `check_hallucination` already judges whether an answer is
grounded in the retrieved documents, but when it says NO and the
rewrite-retry budget is exhausted, the graph previously shipped the
ungrounded answer as-is (see `graph.py::_decide_after_hallucination_
check`'s old "accept anyway" fallthrough). That's exactly the failure
mode 2026 agentic-RAG research flags as most dangerous: "fluent,
well-cited, but built on a fiction" — the answer LOOKS fine with
nothing to signal otherwise.

This prompt's job is narrow: given ONE ungrounded answer and a batch
of live web search results already fetched for it, decide only
whether those results corroborate, contradict, or say nothing
decisive — never regenerate the answer, never fabricate a verdict
when the evidence is thin (that's what 'unclear' is for)."""
from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate


CORROBORATION_PROMPT_VERSION = "crag-ambiguous-corroboration-2026-09-16"


CORROBORATION_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are a fact-checker. An AI assistant answered a question, "
        "but an automated grounding check couldn't verify the answer "
        "against the indexed source documents — it may be correct, "
        "but it isn't confirmed. You're given live web search results "
        "gathered for the same question. Your ONLY job is to judge "
        "whether those web results corroborate, contradict, or say "
        "nothing decisive about the answer's claims.\n\n"
        "RULES:\n"
        "- 'corroborates' — the web results support the answer's "
        "  main claims.\n"
        "- 'contradicts' — the web results conflict with the answer's "
        "  main claims.\n"
        "- 'unclear' — the web results are off-topic, too thin, or "
        "  genuinely don't confirm or deny the claims. Don't force a "
        "  verdict when the evidence doesn't support one.\n"
        "- `note` is ONE short sentence for the end user, plain and "
        "  direct — no hedging filler. Empty string when verdict is "
        "  'unclear' and there's nothing worth surfacing.\n"
        "- Never rewrite or repeat the answer itself — you're judging "
        "  it, not regenerating it.",
    ),
    (
        "human",
        "Question: {question}\n\n"
        "Answer to check: {generation}\n\n"
        "Live web search results:\n{web_context}\n\n"
        "Judge the answer against these results.",
    ),
])
