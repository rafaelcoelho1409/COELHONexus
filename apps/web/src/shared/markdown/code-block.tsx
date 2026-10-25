"use client";

import { Check, Copy } from "lucide-react";
import { type ReactNode, useEffect, useRef, useState } from "react";

import { Button } from "@/shared/ui/button";

const CONFIRM_MS = 2_000;

/** Copies `text` and says so for a moment; "failed" when the browser refuses (insecure context, no permission). */
function useCopy(text: string) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  useEffect(() => () => clearTimeout(timer.current), []);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setState("copied");
    } catch {
      setState("failed");
    }
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setState("idle"), CONFIRM_MS);
  };
  return { state, copy };
}

/** The language badge and copy button above a code block. The block's own scroll area is focusable, so a long
 * line can be scrolled with the keyboard. */
export function CodeBlock({
  language,
  raw,
  children,
}: {
  language: string | null;
  raw: string;
  children: ReactNode;
}) {
  const { state, copy } = useCopy(raw);
  return (
    <figure className="md-code">
      <figcaption className="md-code-bar">
        <span>{language ?? "text"}</span>
        <Button variant="ghost" size="sm" onClick={() => void copy()} aria-label="Copy code">
          {state === "copied" ? <Check aria-hidden /> : <Copy aria-hidden />}
          <span aria-live="polite">
            {state === "copied" ? "Copied" : state === "failed" ? "Copy failed" : "Copy"}
          </span>
        </Button>
      </figcaption>
      {/* biome-ignore lint/a11y/noNoninteractiveTabindex: long lines scroll sideways, so keyboard users must be able to focus the block to scroll it (WCAG 2.1.1) */}
      <pre tabIndex={0}>{children}</pre>
    </figure>
  );
}
