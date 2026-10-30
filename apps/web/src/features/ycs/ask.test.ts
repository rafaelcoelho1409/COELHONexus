import { describe, expect, it } from "vitest";

import {
  applyAskEvent,
  citationThumb,
  citationUrl,
  citeMarkdown,
  citeNumber,
  formatDuration,
  isTurnInProgress,
  lastSourcedTurn,
  newLiveTurn,
  scopeLabel,
  thinkingLabel,
  turnFromHistory,
} from "./ask";
import type { AskCitation, AskEvent, HistoryRow } from "./entities";

const cite = (title: string, extra: Partial<AskCitation> = {}): AskCitation => ({
  title,
  url: "",
  video_id: "",
  channel: "",
  snippet: "",
  source: "",
  ...extra,
});

const event = (partial: Partial<AskEvent> & { node: string }): AskEvent => partial as AskEvent;

describe("citeMarkdown", () => {
  const citations = [cite("Redis in practice"), cite("Postgres deep dive")];

  it("turns a [Video: title] into a numbered link, matching on title", () => {
    expect(citeMarkdown("Fast [Video: Redis in practice] and [Video: postgres deep dive].", citations)).toBe(
      "Fast [1](#cite-1) and [2](#cite-2).",
    );
  });

  it("falls back to a substring match when the model paraphrased the title", () => {
    expect(citeMarkdown("See [Video: Redis in practice (2024)]", citations)).toBe("See [1](#cite-1)");
  });

  it("leaves an unknown title as it was written", () => {
    expect(citeMarkdown("See [Video: Something else]", citations)).toBe("See [Video: Something else]");
  });

  it("does nothing without sources", () => {
    expect(citeMarkdown("See [Video: A]", [])).toBe("See [Video: A]");
  });
});

describe("citeNumber", () => {
  it("reads a pill link and ignores every other href", () => {
    expect(citeNumber("#cite-12")).toBe(12);
    expect(citeNumber("https://example.test/#cite-1")).toBeNull();
    expect(citeNumber("#top")).toBeNull();
    expect(citeNumber(undefined)).toBeNull();
  });
});

describe("source helpers", () => {
  it("opens the source's own URL, else the watch page, else nothing", () => {
    expect(citationUrl(cite("a", { url: "https://youtube.test/watch?v=x" }))).toBe(
      "https://youtube.test/watch?v=x",
    );
    expect(citationUrl(cite("a", { video_id: "abc" }))).toBe("https://www.youtube.com/watch?v=abc");
    expect(citationUrl(cite("a"))).toBe("");
  });

  it("derives the thumbnail from the video id", () => {
    expect(citationThumb(cite("a", { video_id: "abc" }))).toBe("https://i.ytimg.com/vi/abc/mqdefault.jpg");
    expect(citationThumb(cite("a"))).toBe("");
  });

  it("picks the latest turn that has sources", () => {
    expect(lastSourcedTurn([{ citations: [cite("a")] }, { citations: [] }])).toBe(0);
    expect(lastSourcedTurn([{ citations: [] }])).toBeNull();
    expect(lastSourcedTurn([])).toBeNull();
  });
});

describe("thinkingLabel", () => {
  it("says Thinking while it runs and how long it took after", () => {
    expect(thinkingLabel({ streaming: true, recovered: false, durationMs: null })).toBe("Thinking");
    expect(thinkingLabel({ streaming: false, recovered: true, durationMs: null })).toBe("Thinking");
    expect(thinkingLabel({ streaming: false, recovered: false, durationMs: 14_000 })).toBe(
      "Thought for 14.0s",
    );
    expect(thinkingLabel({ streaming: false, recovered: false, durationMs: null })).toBe("Thinking trace");
  });
});

describe("scope, duration and turn construction", () => {
  it("labels the scope", () => {
    const name = (id: string) => `Channel ${id}`;
    expect(scopeLabel([], name, true)).toBe("All channels");
    expect(scopeLabel(["a"], name, true)).toBe("Channel a");
    expect(scopeLabel(["a", "b"], name, true)).toBe("Channel a, Channel b");
    expect(scopeLabel(["a", "b", "c"], name, true)).toBe("Channel a +2 more");
    expect(scopeLabel(["a"], name, false)).toBe("Loading channel…");
  });

  it("formats durations in buckets", () => {
    expect(formatDuration(400)).toBe("<1s");
    expect(formatDuration(12_400)).toBe("12.4s");
    expect(formatDuration(134_000)).toBe("2m 14s");
    expect(formatDuration(null)).toBe("");
  });

  it("gives every live turn its own key and remembers its scope", () => {
    const a = newLiveTurn("one", ["c1"]);
    const b = newLiveTurn("two");
    expect(a.key).not.toBe(b.key);
    expect(a.channelIds).toEqual(["c1"]);
    expect(b.channelIds).toBeNull();
  });
});

describe("applyAskEvent", () => {
  it("walks the stages and fills the answer, sources and timing", () => {
    let turn = newLiveTurn("What is Redis?");
    turn = applyAskEvent(turn, event({ node: "_meta", turn_id: 7 }));
    turn = applyAskEvent(turn, event({ node: "retrieve", mode: "standard" }));
    expect(turn.stages.retrieve.status).toBe("active");
    expect(turn.stages.retrieve.action).toBe("Searching transcripts");
    turn = applyAskEvent(turn, event({ node: "grade_documents" }));
    expect(turn.stages.retrieve.status).toBe("done");
    expect(turn.stages.grade.status).toBe("active");
    turn = applyAskEvent(
      turn,
      event({
        node: "generate",
        generation: "Redis is a store. [Video: Redis in practice]",
        citations: [cite("Redis in practice")],
      }),
    );
    turn = applyAskEvent(turn, event({ node: "end", duration_ms: 9000 }));
    expect(turn).toMatchObject({ turnId: 7, mode: "standard", streaming: false, durationMs: 9000 });
    expect(turn.stages.verify.status).toBe("done");
    expect(turn.answer).toContain("Redis is a store");
    expect(turn.citations).toHaveLength(1);
  });

  it("builds the Deep plan and flips a sub-question card when its answer arrives", () => {
    let turn = newLiveTurn("Compare");
    turn = applyAskEvent(
      turn,
      event({ node: "plan_research", sub_questions: ["Part A?", "Part B?"], research_plan: "Do A then B" }),
    );
    expect(turn.deep?.banner).toBe("Research plan — 2 sub-questions");
    expect(turn.deep?.cards.map((card) => card.status)).toEqual(["queued", "queued"]);
    turn = applyAskEvent(
      turn,
      event({ node: "run_subagent", sub_results: [{ sub_question: "Part A?", answer: "A is 1" }] }),
    );
    expect(turn.deep?.cards[0]).toMatchObject({ status: "done", answer: "A is 1" });
    expect(turn.deep?.cards[1]?.status).toBe("queued");
  });

  it("records a stream error", () => {
    const turn = applyAskEvent(newLiveTurn("q"), event({ node: "error", error: "boom" }));
    expect(turn).toMatchObject({ streaming: false, error: "boom" });
  });
});

describe("turnFromHistory", () => {
  const row = (partial: Partial<HistoryRow>): HistoryRow => ({
    id: 3,
    question: "q",
    answer: "",
    mode: "fast",
    created_at: "2026-10-29T10:00:00Z",
    thinking_state: null,
    ...partial,
  });

  it("restores a finished turn with every step done and its scope", () => {
    const turn = turnFromHistory(
      row({ answer: "done", thinking_state: { channel_ids: ["c1"], duration_ms: 5000 } }),
    );
    expect(turn.recovered).toBe(false);
    expect(turn.channelIds).toEqual(["c1"]);
    expect(turn.key).toBe("row-3");
    expect(
      Object.values(turn.stages).every((stage) => stage.status === "done" || stage.status === "queued"),
    ).toBe(true);
  });

  it("recognises a turn that was mid-run when the page went away", () => {
    const state = { stages: { retrieve: { status: "active", action: "Searching transcripts" } } };
    expect(isTurnInProgress(state, "")).toBe(true);
    expect(turnFromHistory(row({ thinking_state: state })).recovered).toBe(true);
  });
});
