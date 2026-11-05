import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ContextBar } from "./context-bar";
import { ContextBarSlot } from "./context-bar-slot";

describe("ContextBar", () => {
  it("draws the page's bar into the shell's slot, start on the left and end on the right", () => {
    const { container } = render(
      <>
        <ContextBarSlot />
        <main>
          <ContextBar
            start={<span>Locust · 24 pages · 213.6 KB</span>}
            end={<button type="button">Materials</button>}
          />
        </main>
      </>,
    );

    const slot = container.querySelector("#context-bar");
    expect(slot).toContainElement(screen.getByText("Locust · 24 pages · 213.6 KB"));
    expect(slot).toContainElement(screen.getByRole("button", { name: "Materials" }));
    expect(container.querySelector("main")).not.toContainElement(
      screen.getByText("Locust · 24 pages · 213.6 KB"),
    );
  });

  it("still shows the bar where there is no shell around the page", () => {
    render(<ContextBar start={<span>Alone</span>} />);
    expect(screen.getByText("Alone")).toBeInTheDocument();
  });
});
