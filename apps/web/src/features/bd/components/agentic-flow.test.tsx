import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { agenticNodeViews, EMPTY_AGENTIC_PROGRESS } from "../domain/agentic-progress";
import { AgenticFlow } from "./agentic-flow";
import { AgenticNodeList, AgenticViewSwitch } from "./agentic-parts";

const PROGRESS = {
  ...EMPTY_AGENTIC_PROGRESS,
  current_node: "repair_pages",
  node_status: { audit_batch: "done", plan_repairs: "done", repair_pages: "running" } as const,
  audited_pages: [1, 2, 3, 4],
  processed_pages: [1, 2],
  active_repair_pages: [3],
};

function views() {
  return agenticNodeViews(PROGRESS, "running", 4, "pages", null);
}

// Edges only draw once React Flow has measured its nodes in a real layout engine, so jsdom checks the nodes;
// the highlighted edge is covered by the browser tests.
describe("AgenticFlow", () => {
  it("draws one button per node, named with its state, with a headline number and a live line", () => {
    render(<AgenticFlow views={views()} onOpenNode={() => {}} />);
    const graph = screen.getByRole("figure", { name: "Book Distiller agentic ingestion graph" });
    expect(within(graph).getAllByRole("button")).toHaveLength(5);
    expect(within(graph).getByRole("button", { name: /Audit pages.*done/ })).toHaveTextContent(
      "4 pages audited",
    );
    expect(within(graph).getByRole("button", { name: /Repair pages.*running/ })).toHaveTextContent(
      "2/4 · 1 in flight",
    );
    expect(within(graph).getByRole("button", { name: /Publish results.*pending/ })).toBeInTheDocument();
  });

  it("opens a node by its button, with the keyboard as well as the mouse", async () => {
    const onOpen = vi.fn();
    render(<AgenticFlow views={views()} onOpenNode={onOpen} />);
    const button = screen.getByRole("button", { name: /Plan repairs/ });
    await userEvent.click(button);
    expect(onOpen).toHaveBeenCalledWith("plan_repairs");
    button.focus();
    await userEvent.keyboard("{Enter}");
    expect(onOpen).toHaveBeenCalledTimes(2);
  });

  it("does not make the nodes draggable or focusable twice", () => {
    render(<AgenticFlow views={views()} onOpenNode={() => {}} />);
    const graph = screen.getByRole("figure", { name: "Book Distiller agentic ingestion graph" });
    expect(graph.querySelectorAll(".react-flow__node[tabindex]")).toHaveLength(0);
    expect(graph.querySelectorAll("button")).toHaveLength(5);
  });
});

describe("AgenticNodeList", () => {
  it("lists the same nodes in the same words", async () => {
    const onOpen = vi.fn();
    render(<AgenticNodeList rows={views()} onOpen={onOpen} />);
    const list = screen.getByRole("list", { name: "Agentic pipeline nodes" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(5);
    const repair = within(list).getByRole("button", { name: /Repair pages/ });
    expect(repair).toHaveTextContent("— running");
    expect(repair).toHaveTextContent("2/4 · 1 in flight");
    await userEvent.click(repair);
    expect(onOpen).toHaveBeenCalledWith("repair_pages");
  });
});

describe("AgenticViewSwitch", () => {
  it("shows the chosen view as pressed and reports a change", async () => {
    const onChange = vi.fn();
    render(<AgenticViewSwitch value="graph" onChange={onChange} />);
    expect(screen.getByRole("button", { name: "Graph" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "List" })).toHaveAttribute("aria-pressed", "false");
    await userEvent.click(screen.getByRole("button", { name: "List" }));
    expect(onChange).toHaveBeenCalledWith("list");
  });
});
