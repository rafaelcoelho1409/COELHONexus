import { describe, expect, it } from "vitest";

import { applyAskEvent, citationUrl, citeMarkdown, newLiveTurn, usageModelRows } from "./ask";
import type { AskCitation, AskUsage } from "./entities";

const citation = (over: Partial<AskCitation> = {}): AskCitation => ({
  title: "Consistency and consensus",
  url: "",
  book_id: "book-1",
  page: 42,
  chapter: "Distributed Data",
  section: "Consensus",
  snippet: "A quorum requires agreement from enough replicas.",
  source: "qdrant_hybrid",
  ...over,
});

describe("Book Distiller Ask domain", () => {
  it("turns page and named source markers into numbered citation links", () => {
    const sources = [citation(), citation({ title: "Transactions", page: 77 })];
    expect(
      citeMarkdown(
        "The quorum rule appears on [Page: 42], while isolation is covered in [Source: Transactions].",
        sources,
      ),
    ).toBe("The quorum rule appears on [1](#cite-1), while isolation is covered in [2](#cite-2).");
  });

  it("opens an indexed page in the existing Book Distiller reader", () => {
    expect(citationUrl(citation())).toBe("/bd/pages?book=book-1&page=42");
    expect(citationUrl(citation({ url: "https://example.test/source" }))).toBe("https://example.test/source");
  });

  it("keeps the book scope and folds stream events into one turn", () => {
    const turn = newLiveTurn("What is consensus?", "book-1");
    const retrieved = applyAskEvent(turn, {
      node: "retrieve",
      turn_id: null,
      mode: "standard",
      documents: [],
      document_count: 2,
      generation: null,
      confidence_score: null,
      citations: null,
      sub_questions: null,
      research_plan: null,
      sub_results: null,
      latest_sub_question: null,
      latest_sub_answer: null,
      latest_sub_answer_preview: null,
      duration_ms: null,
      usage: null,
      error: null,
    });
    expect(retrieved.bookId).toBe("book-1");
    expect(retrieved.mode).toBe("standard");
    expect(retrieved.stages.retrieve.status).toBe("done");
  });

  it("aggregates usage across graph nodes by runtime model id", () => {
    const usage: AskUsage = {
      total: { calls: 2, tokens_in: 12, tokens_out: 5, reasoning_tokens: 1 },
      by_node: {
        retrieve: {
          by_model: {
            "vendor-a/model-x": { calls: 1, tokens_in: 4, tokens_out: 1, reasoning_tokens: 0 },
          },
        },
        generate: {
          by_model: {
            "vendor-a/model-x": { calls: 1, tokens_in: 8, tokens_out: 4, reasoning_tokens: 1 },
          },
        },
      },
    };
    expect(usageModelRows(usage)).toEqual([
      {
        model: "vendor-a/model-x",
        calls: 2,
        tokens_in: 12,
        tokens_out: 5,
        reasoning_tokens: 1,
      },
    ]);
  });
});
