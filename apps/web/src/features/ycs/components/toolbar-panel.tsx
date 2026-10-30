"use client";

/** A toolbar button that opens a panel under it: Escape or a press outside closes it, and Escape hands focus back to the
 * button. Not modal — the page stays usable, which is what a picker that changes one setting wants. */

import { ChevronDown } from "lucide-react";
import type { JSX, ReactNode } from "react";
import { useEffect, useId, useRef, useState } from "react";

import { Button } from "@/shared/ui/button";

export function ToolbarPanel({
  icon,
  label,
  align = "left",
  children,
}: {
  icon: ReactNode;
  /** The button's text — it says what the setting is now. */
  label: string;
  /** Which edge of the button the panel lines up with; `phone-right` is `right` on a phone and `left` from `sm` up, for a
   * picker that sits at the right end of a phone's row. */
  align?: "left" | "right" | "phone-right";
  children: (close: () => void) => ReactNode;
}): JSX.Element {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const panelId = useId();

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Element;
      // A press inside a dialog (the delete confirmation this panel launched) is not a press "outside".
      if (!rootRef.current?.contains(target) && !target.closest("dialog")) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setOpen(false);
      buttonRef.current?.focus();
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  return (
    <div ref={rootRef} className="relative">
      <Button
        ref={buttonRef}
        type="button"
        variant="outline"
        size="lg"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((on) => !on)}
        className="w-full max-w-full justify-between sm:w-auto sm:justify-center"
      >
        {icon}
        <span className="min-w-0 truncate">{label}</span>
        <ChevronDown aria-hidden className="size-3.5 opacity-60" />
      </Button>
      {open ? (
        <div
          id={panelId}
          className={`absolute top-full z-30 mt-1 w-[min(24rem,calc(100vw-2rem))] rounded-sm border border-border bg-background p-2 ${
            align === "right"
              ? "right-0"
              : align === "phone-right"
                ? "right-0 sm:right-auto sm:left-0"
                : "left-0"
          }`}
        >
          {children(() => setOpen(false))}
        </div>
      ) : null}
    </div>
  );
}
