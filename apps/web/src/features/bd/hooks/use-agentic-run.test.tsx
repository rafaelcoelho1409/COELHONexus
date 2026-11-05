import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AgenticEvent, AgenticProgress, BookSnapshot } from "../entities";
import { useAgenticRun } from "./use-agentic-run";

const streamState = vi.hoisted(() => ({
  onEvent: null as ((event: unknown) => void) | null,
}));

vi.mock("@tanstack/react-query", () => ({
  useQuery: vi.fn(),
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
}));

vi.mock("@/shared/sse", () => ({
  useEventStream: (options: unknown, onEvent: (event: unknown) => void) => {
    streamState.onEvent = onEvent;
    return { status: options === null ? "closed" : "open", error: null };
  },
}));

const emptyProgress: AgenticProgress = {
  current_node: null,
  node_status: {},
  audited_pages: [],
  audit_warning_pages: [],
  processed_pages: [],
  active_audit_pages: [],
  active_repair_pages: [],
};

function snapshot(status: "failed" | "running", progress = emptyProgress): BookSnapshot {
  return {
    agentic: {
      book_id: "book",
      extraction_run_id: "extract",
      agentic_run_id: "run",
      status,
      expected_page_count: 275,
      published_page_count: 0,
      quality_status: null,
      delivered_page_count: 0,
      discarded_blank_page_count: 0,
      review_page_count: 0,
      progress,
      created_at: 1,
      updated_at: status === "failed" ? 2 : 3,
      finished_at: status === "failed" ? 2 : null,
      error: status === "failed" ? "Connection error." : null,
    },
  } as BookSnapshot;
}

describe("useAgenticRun retry state", () => {
  beforeEach(() => {
    streamState.onEvent = null;
  });

  it("clears a previous failure and follows the resumed attempt", () => {
    const { result, rerender } = renderHook(({ currentSnapshot }) => useAgenticRun("book", currentSnapshot), {
      initialProps: { currentSnapshot: snapshot("running") },
    });

    act(() => {
      streamState.onEvent?.({
        kind: "run_failed",
        status: "failed",
        error: "Connection error.",
        progress: emptyProgress,
      } satisfies AgenticEvent);
    });
    expect(result.current.status).toBe("failed");
    expect(result.current.error).toBe("Connection error.");

    rerender({ currentSnapshot: snapshot("failed") });
    act(() => result.current.restart());
    expect(result.current.status).toBe("queued");
    expect(result.current.error).toBeNull();
    expect(result.current.streaming).toBe(true);

    const resumedProgress = {
      ...emptyProgress,
      audited_pages: Array.from({ length: 42 }, (_, index) => index + 1),
    };
    act(() => {
      streamState.onEvent?.({
        kind: "run_queued",
        status: "queued",
        progress: resumedProgress,
      } satisfies AgenticEvent);
    });
    expect(result.current.error).toBeNull();
    expect(result.current.progress?.audited_pages).toHaveLength(42);

    act(() => {
      streamState.onEvent?.({
        kind: "node",
        node: "audit_batch",
        status: "running",
        progress: resumedProgress,
      } satisfies AgenticEvent);
    });
    expect(result.current.status).toBe("running");
  });

  it("shows a requested stop until the run reports its cancelled terminal state", () => {
    const { result } = renderHook(() => useAgenticRun("book", snapshot("running")));

    act(() => {
      streamState.onEvent?.({
        kind: "run_cancelling",
        status: "cancelling",
        progress: emptyProgress,
      } satisfies AgenticEvent);
    });
    expect(result.current.status).toBe("running");
    expect(result.current.cancelRequested).toBe(true);
    expect(result.current.streaming).toBe(true);

    act(() => {
      streamState.onEvent?.({
        kind: "run_cancelled",
        status: "cancelled",
        progress: emptyProgress,
      } satisfies AgenticEvent);
    });
    expect(result.current.status).toBe("cancelled");
    expect(result.current.cancelRequested).toBe(false);
    expect(result.current.streaming).toBe(false);
  });
});
