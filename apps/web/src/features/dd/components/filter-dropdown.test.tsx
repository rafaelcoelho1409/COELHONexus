import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { FilterDropdown, type FilterOption } from "./filter-dropdown";

const OPTIONS: FilterOption[] = [
  { value: "1", label: "1", count: 13, active: false },
  { value: "2", label: "2", count: 6, active: true },
];

function setup(multiple: boolean) {
  const onToggle = vi.fn();
  const onReset = vi.fn();
  render(
    <FilterDropdown
      label="Tier"
      summary="2"
      ariaLabel="Filter by tier"
      options={OPTIONS}
      multiple={multiple}
      onToggle={onToggle}
      onReset={onReset}
    />,
  );
  return { onToggle, onReset, trigger: screen.getByRole("button", { name: "Filter by tier: 2" }) };
}

describe("FilterDropdown", () => {
  it("reads like the toolbar button it replaces, and opens a list with counts", async () => {
    const { trigger } = setup(true);
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("group", { name: "Filter by tier" })).not.toBeInTheDocument();

    await userEvent.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    const list = screen.getByRole("group", { name: "Filter by tier" });
    expect(within(list).getByRole("button", { name: /^1\s*13$/ })).toHaveAttribute("aria-pressed", "false");
    expect(within(list).getByRole("button", { name: /^2\s*6$/ })).toHaveAttribute("aria-pressed", "true");
  });

  it("keeps a multi-choice list open while options are toggled, and Reset clears", async () => {
    const { trigger, onToggle, onReset } = setup(true);
    await userEvent.click(trigger);
    await userEvent.click(screen.getByRole("button", { name: /^1\s*13$/ }));
    expect(onToggle).toHaveBeenCalledWith("1");
    expect(screen.getByRole("group", { name: "Filter by tier" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Reset" }));
    expect(onReset).toHaveBeenCalledOnce();
  });

  it("closes a single-choice list as soon as one is picked, and has no Reset", async () => {
    const { trigger, onToggle } = setup(false);
    await userEvent.click(trigger);
    expect(screen.queryByRole("button", { name: "Reset" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /^1\s*13$/ }));
    expect(onToggle).toHaveBeenCalledWith("1");
    expect(screen.queryByRole("group", { name: "Filter by tier" })).not.toBeInTheDocument();
  });

  it("closes on Escape with focus back on the button, and on a click elsewhere", async () => {
    const { trigger } = setup(true);
    await userEvent.click(trigger);
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("group", { name: "Filter by tier" })).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();

    await userEvent.click(trigger);
    await userEvent.click(document.body);
    expect(screen.queryByRole("group", { name: "Filter by tier" })).not.toBeInTheDocument();
  });
});
