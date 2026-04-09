"""
Document Relevance Grader

CONCEPT: with_structured_output() is the LangChain 1.2 way to get typed responses.
You pass a Pydantic model, and the LLM returns an instance of that model.
Under the hood, it uses function calling / tool use to ensure valid JSON output.

The grader evaluates each retrieved document against the user's question.
Documents scoring "relevant" proceed to generation; others are filtered out.
If no documents pass, the agent rewrites the query and retries.
"""
from pydantic import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.documents import Document
from langchain_openai import ChatOpenAI


class GradeResult(BaseModel):
    """Binary relevance grade for a document."""
    score: str = Field(
        description="'relevant' if the document answers the question, 'not_relevant' otherwise"
    )


GRADING_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are a relevance grader. Given a user question and a retrieved document, "
        "determine if the document contains information relevant to answering the question. "
        "Respond with 'relevant' or 'not_relevant'. "
        "A document is relevant if it contains ANY information that helps answer the question, "
        "even partially.",
    ),
    (
        "human",
        "Question: {question}\n\nDocument content:\n{document}",
    ),
])


class DocumentGrader:
    """Grades document relevance using LLM structured output."""

    def __init__(self, llm: ChatOpenAI):
        # with_structured_output wraps the LLM to return GradeResult instances
        self.grader = GRADING_PROMPT | llm.with_structured_output(GradeResult)

    async def grade_documents(
        self,
        question: str,
        documents: list[Document],
    ) -> list[Document]:
        """
        Grade each document for relevance. Returns only relevant documents.
        """
        relevant = []
        for doc in documents:
            result: GradeResult = await self.grader.ainvoke({
                "question": question,
                "document": doc.page_content[:2000],  # Truncate to avoid token limits
            })
            if result.score == "relevant":
                relevant.append(doc)
        return relevant
