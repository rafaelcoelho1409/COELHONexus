import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { client } from "@/shared/api";
import { QueryProvider } from "@/shared/providers/query-provider";
import type { NodeStatus } from "../domain/stages";
import { PLANNER_NODES } from "../domain/stages";
import type { StageEvent } from "../entities";
import { PLANNER_FINISHED_VALUES } from "../test-fixtures";
import { NodeDrawer } from "./node-drawer";

const EVENTS: StageEvent[] = [
  { step: "off_topic", kind: "start", files: 12, ts: 1_700_000_000 },
  { step: "corpus_load", kind: "done", files: 12, total_bytes: 20_480, ts: 1_700_000_001 },
  { step: "off_topic", kind: "done", kept: 10, total: 12, wall_ms: 3200, ts: 1_700_000_002 },
];

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
beforeEach(() => client.setConfig({ baseUrl: "http://nexus.test" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function drawerFor(
  nodeId: string,
  status: NodeStatus,
  values: Record<string, unknown>,
  events = EVENTS,
  threadId: string | null = null,
) {
  const node = PLANNER_NODES.find((n) => n.id === nodeId);
  if (!node) throw new Error(`no node ${nodeId}`);
  const onClose = vi.fn();
  render(
    <QueryProvider>
      <NodeDrawer
        stage="planner"
        view={{ node, status }}
        values={values}
        events={events}
        threadId={threadId}
        live={false}
        onClose={onClose}
      />
    </QueryProvider>,
  );
  return { onClose, dialog: screen.getByRole("dialog") };
}

describe("NodeDrawer", () => {
  it("adds the node's LLM usage once the run's counters load, without naming any model", async () => {
    server.use(
      http.get("*/api/v1/dd/planner/debug/graph/*/llm-counters", () =>
        HttpResponse.json({
          total: { calls: 12, tokens_in: 6000, tokens_out: 300, reasoning_tokens: 0 },
          by_node: {
            off_topic: {
              calls: 12,
              tokens_in: 6000,
              tokens_out: 300,
              reasoning_tokens: 0,
              by_model: { "some-provider/some-model": { calls: 12 } },
            },
          },
        }),
      ),
    );
    const { dialog } = drawerFor("off_topic", "done", PLANNER_FINISHED_VALUES, EVENTS, "planner/alpha/old");
    expect(await within(dialog).findByText("6,000")).toBeInTheDocument();
    expect(within(dialog).getByText("input tokens")).toBeInTheDocument();
    expect(dialog.textContent).not.toContain("some-model");
  });

  it("explains a finished node and shows what it produced", () => {
    const { dialog } = drawerFor("off_topic", "done", PLANNER_FINISHED_VALUES);
    expect(within(dialog).getByRole("heading", { name: "Off-topic filter" })).toBeInTheDocument();
    expect(within(dialog).getByText("LLM judge")).toBeInTheDocument();
    expect(within(dialog).getByText(/keeps only the pages relevant/)).toBeInTheDocument();
    expect(within(dialog).getByText("relevant_files")).toBeInTheDocument(); // what it writes
    // headline numbers, then the rich overview (verdict table)
    expect(within(dialog).getByText("LLM calls")).toBeInTheDocument();
    expect(within(dialog).getAllByText("12").length).toBeGreaterThan(0);
    expect(within(dialog).getByRole("region", { name: /LLM verdict/ })).toBeInTheDocument();
  });

  it("tells the reader a node that has not run has nothing yet", () => {
    const { dialog } = drawerFor("plan_write", "pending", {});
    expect(within(dialog).getByText("This node has not run yet.")).toBeInTheDocument();
    expect(within(dialog).queryByRole("region")).not.toBeInTheDocument();
  });

  it("lists only this node's events in Activity, newest last, and counts the unseen ones", async () => {
    const { dialog } = drawerFor("off_topic", "running", {});
    const activity = within(dialog).getByRole("tab", { name: /Activity/ });
    expect(activity).toHaveAccessibleName(/2 new/);

    await userEvent.click(activity);
    const log = await within(dialog).findByRole("log", { name: "Node events" });
    const lines = within(log).getAllByRole("listitem");
    expect(lines).toHaveLength(2); // the corpus_load event belongs to another node
    expect(lines[0]).toHaveTextContent("· filtering 12 files…");
    expect(lines[1]).toHaveTextContent("✓ kept 10/12 (3200 ms)");
    expect(within(dialog).getByRole("tab", { name: /Activity/ })).not.toHaveAccessibleName(/new/);
  });

  it("says so when a node has no events yet", async () => {
    const { dialog } = drawerFor("plan_write", "pending", {}, EVENTS);
    await userEvent.click(within(dialog).getByRole("tab", { name: /Activity/ }));
    expect(await within(dialog).findByText(/No events for this node/)).toBeInTheDocument();
  });

  it("shows the raw state a node reads and writes", async () => {
    const { dialog } = drawerFor("off_topic", "done", PLANNER_FINISHED_VALUES);
    await userEvent.click(within(dialog).getByRole("tab", { name: "Raw I/O" }));
    expect(await within(dialog).findByText("Inputs (upstream state, raw)")).toBeInTheDocument();
    expect(within(dialog).getByText(/"off_topic_stats"/)).toBeInTheDocument();
  });

  it("closes from the button and from Escape", async () => {
    const first = drawerFor("off_topic", "done", PLANNER_FINISHED_VALUES);
    await userEvent.click(within(first.dialog).getByRole("button", { name: "Close node details" }));
    expect(first.onClose).toHaveBeenCalledTimes(1);

    fireEvent(first.dialog, new Event("cancel", { cancelable: true }));
    expect(first.onClose).toHaveBeenCalledTimes(2);
  });
});
