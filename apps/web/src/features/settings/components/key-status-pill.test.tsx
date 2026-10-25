import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { KeyStatus } from "../entities";
import { KeyStatusPill } from "./key-status-pill";

describe("KeyStatusPill", () => {
  it.each([
    [{ has_key: true, source: "user", last4: "abcd" }, "text-success-text", "Custom key ••••abcd"],
    [{ has_key: true, source: "env", last4: "ef01" }, "text-warning-text", "Env key ••••ef01"],
    [{ has_key: false, source: null, last4: null }, "text-muted-foreground", "No key"],
  ] as const)("renders %s as %s", (status: KeyStatus, toneClass: string, text: string) => {
    render(<KeyStatusPill kind="llm" status={status} />);
    const pill = screen.getByText(text);
    expect(pill).toHaveClass(toneClass);
  });

  it("drops the dots entirely when last4 is missing", () => {
    render(<KeyStatusPill kind="embedding" status={{ has_key: true, source: "user", last4: null }} />);
    expect(screen.getByText("Custom key")).toBeInTheDocument();
  });
});
