"""
Document Relevance Grader

CONCEPT: with_structured_output() is the LangChain 1.2 way to get typed responses.
You pass a Pydantic model, and the LLM returns an instance of that model.
Under the hood, it uses function calling / tool use to ensure valid JSON output.

The grader evaluates each retrieved document against the user's question.
Documents scoring "relevant" proceed to generation; others are filtered out.
If no documents pass, the agent rewrites the query and retries.

With the fallback chain (8 models × 40 RPM = ~320 RPM), we can grade
documents in PARALLEL again without hitting rate limits.
"""
import asyncio
from langchain_core.documents import Document

from schemas.youtube.agents import GradeResult
from schemas.youtube.prompts import GRADING_PROMPT


class DocumentGrader:
    """Grades document relevance using LLM structured output."""
    def __init__(self, llm):
        # llm is a RunnableWithFallbacks (from with_fallbacks()).
        # with_structured_output works on the underlying Runnable chain.
        # method="function_calling" ensures compatibility with all NVIDIA NIM models.
        self.grader = GRADING_PROMPT | llm.with_structured_output(
            GradeResult, 
            method = "function_calling")

    async def grade_documents(
        self,
        question: str,
        documents: list[Document],
    ) -> list[Document]:
        """
        Grade all documents in PARALLEL.

        CONCEPT: With the fallback chain (8 models × ~40 RPM each = ~320 RPM),
        parallel grading is safe again. If model #1 returns 429 on one call,
        with_fallbacks() transparently routes that call to model #2.

        asyncio.gather runs all LLM calls concurrently.
        return_exceptions=True prevents one failure from killing all grades.
        """
        if not documents:
            return []
        tasks = [
            self.grader.ainvoke({
                "question": question,
                "document": doc.page_content[:2000],
            })
            for doc in documents
        ]
        results = await asyncio.gather(*tasks, return_exceptions = True)
        relevant = []
        for doc, result in zip(documents, results):
            if isinstance(result, Exception):
                print(f"[grader] Failed: {result}", flush = True)
                continue
            if result.score == "relevant":
                relevant.append(doc)
        return relevant
