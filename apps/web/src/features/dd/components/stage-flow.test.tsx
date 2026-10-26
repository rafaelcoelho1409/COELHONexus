import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { SYNTH_NODES, stageNodeViews } from "../domain/stages";
import { StageFlow } from "./stage-flow";

// Edges only draw once React Flow has measured its nodes in a real layout engine, so jsdom checks the nodes;
// the edges and the refine loop are covered by the browser tests (e2e/dd.spec.ts).
function renderSynthGraph(
  values: Record<string, unknown>,
  onOpen: (id: string) => void = () => {},
  running = false,
) {
  const implemented = new Set(SYNTH_NODES.map((node) => node.id));
  const views = stageNodeViews(SYNTH_NODES, values, [], running, implemented);
  return render(
    <StageFlow
      stage="synth"
      title="Synth"
      views={views}
      kpis={{ sawc_write: "4 sections" }}
      liveLines={{}}
      values={values}
      live={running}
      onOpen={onOpen}
    />,
  );
}

describe("StageFlow", () => {
  it("draws one button per node, named with its state, and shows a finished node's headline number", () => {
    renderSynthGraph({ outline_path: "x", sawc_path: "y" });
    const graph = screen.getByRole("figure", { name: "Synth graph" });
    expect(within(graph).getAllByRole("button")).toHaveLength(SYNTH_NODES.length);
    expect(within(graph).getByRole("button", { name: /Outline \(SDP\).*done/ })).toBeInTheDocument();
    expect(within(graph).getByRole("button", { name: /Digest.*pending/ })).toBeInTheDocument();
    expect(within(graph).getByText("4 sections")).toBeInTheDocument();
  });

  it("opens a node by its button, with the keyboard as well as the mouse", async () => {
    const onOpen = vi.fn();
    renderSynthGraph({}, onOpen);
    const button = screen.getByRole("button", { name: /SAWC write/ });
    await userEvent.click(button);
    expect(onOpen).toHaveBeenCalledWith("sawc_write");
    button.focus();
    await userEvent.keyboard("{Enter}");
    expect(onOpen).toHaveBeenCalledTimes(2);
  });

  it("does not make the nodes draggable or focusable twice", () => {
    renderSynthGraph({});
    const graph = screen.getByRole("figure", { name: "Synth graph" });
    expect(graph.querySelectorAll(".react-flow__node[tabindex]")).toHaveLength(0);
    expect(graph.querySelectorAll("button")).toHaveLength(SYNTH_NODES.length);
  });
});
