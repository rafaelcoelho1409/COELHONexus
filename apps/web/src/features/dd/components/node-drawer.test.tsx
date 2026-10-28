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

const COUNTERS = {
  total: { calls: 14, tokens_in: 9000, tokens_out: 2100, reasoning_tokens: 0, errors: 3 },
  by_node: {
    off_topic: {
      calls: 12,
      tokens_in: 6000,
      tokens_out: 300,
      reasoning_tokens: 0,
      errors: 2,
      by_model: {
        "vendor-a/model-small": { calls: 9, tokens_in: 4500, tokens_out: 200, reasoning_tokens: 0 },
        "vendor-b/model-large": { calls: 3, tokens_in: 1500, tokens_out: 100, reasoning_tokens: 0 },
      },
    },
  },
};

function drawerFor(
  nodeId: string,
  status: NodeStatus,
  values: Record<string, unknown>,
  events = EVENTS,
  threadId: string | null = null,
  since = 0,
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
        since={since}
        onClose={onClose}
      />
    </QueryProvider>,
  );
  return { onClose, dialog: screen.getByRole("dialog") };
}

describe("NodeDrawer", () => {
  it("adds the node's LLM usage once the run's counters load, with its busiest model", async () => {
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
              by_model: { "vendor-a/model-small": { calls: 12, tokens_in: 6000, tokens_out: 300 } },
            },
          },
        }),
      ),
    );
    const { dialog } = drawerFor("off_topic", "done", PLANNER_FINISHED_VALUES, EVENTS, "planner/alpha/old");
    // the headline figure, then the usage table's node and whole-run cells, then the model's own row
    expect(await within(dialog).findAllByText("6,000")).toHaveLength(4);
    expect(within(dialog).getByText("input tokens")).toBeInTheDocument();
    expect(within(dialog).getByText("top model")).toBeInTheDocument();
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

  it("tabulates the node's LLM calls and tokens against the whole run", async () => {
    server.use(http.get("*/api/v1/dd/planner/debug/graph/*/llm-counters", () => HttpResponse.json(COUNTERS)));
    const { dialog } = drawerFor("off_topic", "done", PLANNER_FINISHED_VALUES, EVENTS, "planner/alpha/old");

    const table = await within(dialog).findByRole("region", { name: "LLM calls and tokens" });
    expect(within(table).getByRole("columnheader", { name: "This node" })).toBeInTheDocument();
    expect(within(table).getByRole("columnheader", { name: "Whole run" })).toBeInTheDocument();
    const failed = within(table).getByRole("row", { name: /Failed calls/ });
    expect(
      within(failed)
        .getAllByRole("cell")
        .map((cell) => cell.textContent),
    ).toEqual(["2", "3", "67%"]);
    expect(within(table).getByRole("row", { name: /Input per call/ })).toHaveTextContent("500");
    // the headline grid shows the exact usage once, not the node's own "LLM calls" beside it
    expect(within(dialog).getAllByText("LLM calls")).toHaveLength(1);
  });

  it("names each model that answered, with its calls and input, output and reasoning tokens, busiest first", async () => {
    server.use(http.get("*/api/v1/dd/planner/debug/graph/*/llm-counters", () => HttpResponse.json(COUNTERS)));
    const { dialog } = drawerFor("off_topic", "done", PLANNER_FINISHED_VALUES, EVENTS, "planner/alpha/old");

    const table = await within(dialog).findByRole("region", { name: "Usage by model" });
    expect(
      within(table)
        .getAllByRole("row")
        .map((row) => row.textContent),
    ).toEqual([
      "ModelCallsInput tokensOutput tokensReasoning",
      "vendor-a/model-small94,5002000",
      "vendor-b/model-large31,5001000",
    ]);
    expect(within(dialog).getByText("vendor-a/model-small", { selector: "dd" })).toBeInTheDocument(); // top model
    expect(within(dialog).getByText("9 calls")).toBeInTheDocument();
  });

  it("puts the by-model table above the calls-and-tokens table", async () => {
    server.use(http.get("*/api/v1/dd/planner/debug/graph/*/llm-counters", () => HttpResponse.json(COUNTERS)));
    const { dialog } = drawerFor("off_topic", "done", PLANNER_FINISHED_VALUES, EVENTS, "planner/alpha/old");
    const byModel = await within(dialog).findByRole("region", { name: "Usage by model" });
    const callsAndTokens = within(dialog).getByRole("region", { name: "LLM calls and tokens" });
    expect(byModel.compareDocumentPosition(callsAndTokens) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("has no by-model table for a node that made no call", async () => {
    server.use(http.get("*/api/v1/dd/planner/debug/graph/*/llm-counters", () => HttpResponse.json(COUNTERS)));
    const { dialog } = drawerFor("corpus_load", "done", PLANNER_FINISHED_VALUES, EVENTS, "planner/alpha/old");
    expect(await within(dialog).findByRole("heading", { name: "Corpus load" })).toBeInTheDocument();
    expect(within(dialog).queryByRole("region", { name: "Usage by model" })).not.toBeInTheDocument();
  });

  it("has no calls-and-tokens table before the counters load or for a node that made no call", async () => {
    server.use(http.get("*/api/v1/dd/planner/debug/graph/*/llm-counters", () => HttpResponse.json(COUNTERS)));
    const { dialog } = drawerFor("corpus_load", "done", PLANNER_FINISHED_VALUES, EVENTS, "planner/alpha/old");
    expect(await within(dialog).findByRole("heading", { name: "Corpus load" })).toBeInTheDocument();
    expect(within(dialog).queryByRole("region", { name: "LLM calls and tokens" })).not.toBeInTheDocument();
  });

  it("names the stage and node in the header, with the node's headline number once it is done", () => {
    const { dialog } = drawerFor("off_topic", "done", PLANNER_FINISHED_VALUES);
    expect(within(dialog).getByText("planner · off_topic")).toBeInTheDocument();
    expect(within(dialog).getByTestId("node-kpi")).not.toBeEmpty();
    expect(within(dialog).getByTestId("node-kpi")).toHaveTextContent(/\d/);
  });

  it("shows no headline number while the node is still running", () => {
    const { dialog } = drawerFor("plan_write", "running", {});
    expect(within(dialog).queryByTestId("node-kpi")).not.toBeInTheDocument();
  });

  it("opens the tabs from the keys 1, 2 and 3, except while typing or with a modifier held", async () => {
    const { dialog } = drawerFor("off_topic", "done", PLANNER_FINISHED_VALUES);
    const tab = (name: string) => within(dialog).getByRole("tab", { name: new RegExp(name) });

    await userEvent.keyboard("2");
    expect(tab("Activity")).toHaveAttribute("aria-selected", "true");
    await userEvent.keyboard("3");
    expect(tab("Raw")).toHaveAttribute("aria-selected", "true");
    await userEvent.keyboard("{Control>}1{/Control}");
    expect(tab("Raw")).toHaveAttribute("aria-selected", "true");
    await userEvent.keyboard("1");
    expect(tab("Overview")).toHaveAttribute("aria-selected", "true");
    await userEvent.keyboard("9");
    expect(tab("Overview")).toHaveAttribute("aria-selected", "true");

    const field = document.createElement("input");
    document.body.append(field);
    field.focus();
    await userEvent.keyboard("2");
    expect(tab("Overview")).toHaveAttribute("aria-selected", "true");
    field.remove();
  });

  it("marks failures in the log and the lines that arrived since the node was last open", async () => {
    const events: StageEvent[] = [
      { step: "off_topic", kind: "start", files: 12, ts: 1_700_000_000 },
      { step: "off_topic", kind: "error", error: "judge timed out", ts: 1_700_000_005 },
    ];
    const { dialog } = drawerFor("off_topic", "failed", {}, events, null, 1_700_000_001_000);
    await userEvent.click(within(dialog).getByRole("tab", { name: /Activity/ }));
    const lines = within(await within(dialog).findByRole("log", { name: "Node events" })).getAllByRole(
      "listitem",
    );
    expect(lines[0]).toHaveAttribute("data-tone", "info");
    expect(lines[0]).not.toHaveAttribute("data-new");
    expect(lines[1]).toHaveAttribute("data-tone", "error");
    expect(lines[1]).toHaveAttribute("data-new", "true");
    expect(lines[1]).toHaveTextContent("error — judge timed out");
    expect(lines[1]).toHaveTextContent("(error)");
    expect(lines[1]).toHaveTextContent("(new)");
  });

  it("closes from the button and from Escape", async () => {
    const first = drawerFor("off_topic", "done", PLANNER_FINISHED_VALUES);
    await userEvent.click(within(first.dialog).getByRole("button", { name: "Close node details" }));
    expect(first.onClose).toHaveBeenCalledTimes(1);

    fireEvent(first.dialog, new Event("cancel", { cancelable: true }));
    expect(first.onClose).toHaveBeenCalledTimes(2);
  });
});
