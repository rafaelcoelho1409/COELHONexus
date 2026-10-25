import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useClock } from "./use-clock";

describe("useClock", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-25T12:00:00Z"));
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("stays null while inactive", () => {
    const { result } = renderHook(() => useClock(false));
    expect(result.current).toBeNull();
  });

  it("reads the current time once active, then ticks every second", () => {
    const { result } = renderHook(() => useClock(true));
    const first = result.current;
    expect(first).toBe(Date.parse("2026-10-25T12:00:00Z"));

    act(() => {
      vi.advanceTimersByTime(3_000);
    });
    expect(result.current).toBe((first ?? 0) + 3_000);
  });

  it("goes back to null when deactivated and stops the timer", () => {
    const { result, rerender } = renderHook(({ active }) => useClock(active), {
      initialProps: { active: true },
    });
    expect(result.current).not.toBeNull();

    rerender({ active: false });
    expect(result.current).toBeNull();
    expect(vi.getTimerCount()).toBe(0);
  });
});
