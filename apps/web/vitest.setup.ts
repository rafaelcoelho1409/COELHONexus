import "@testing-library/jest-dom/vitest";

import { cleanup, configure } from "@testing-library/react";
import { afterEach, vi } from "vitest";

// `findBy…`/`waitFor` give up after 1 s by default. The first render of a chapter loads the markdown pipeline
// (unified, highlight, KaTeX) inside jsdom, which takes longer than that when the workers run in parallel on a
// busy machine. A test that passes still returns the moment its element appears; this only moves the give-up.
configure({ asyncUtilTimeout: 5_000 });

// jsdom does not implement matchMedia; every real browser does.
if (typeof window !== "undefined" && !window.matchMedia) {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: (query: string): MediaQueryList => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }),
  });
}

afterEach(() => cleanup());
