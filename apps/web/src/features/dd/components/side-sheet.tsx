"use client";

/** A right-hand sheet on the native modal `<dialog>`: focus is trapped, the page behind is inert, Escape or
 * the close button dismisses it, and focus returns to whatever opened it. Render it only while it is open. */

import { X } from "lucide-react";
import { type ReactNode, useEffect, useId } from "react";

import { Button } from "@/shared/ui/button";

import { useModalDialog } from "../hooks/use-modal-dialog";

export function SideSheet({
  title,
  subtitle,
  closeLabel,
  scrollKey,
  onClose,
  children,
}: {
  title: string;
  /** Status line under the title. */
  subtitle?: ReactNode;
  closeLabel: string;
  /** When this changes the sheet scrolls back to its top (a new page was loaded into it). */
  scrollKey?: string | number;
  onClose: () => void;
  children: ReactNode;
}) {
  const ref = useModalDialog();
  const titleId = useId();

  // biome-ignore lint/correctness/useExhaustiveDependencies: `scrollKey` is the trigger, not a value read here
  useEffect(() => {
    ref.current?.scrollTo?.({ top: 0 });
  }, [scrollKey, ref]);

  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      onCancel={(event) => {
        event.preventDefault(); // Escape: close through our state so the parent stays the one source of truth
        onClose();
      }}
      className="m-0 ml-auto h-dvh max-h-none w-full max-w-2xl overflow-y-auto border-l border-border bg-background p-0 text-foreground backdrop:bg-background/80"
    >
      <header className="sticky top-0 z-10 flex items-start gap-3 border-b border-border bg-background px-5 py-4">
        <div className="min-w-0 flex-1">
          <h2 id={titleId} className="text-lg font-semibold tracking-tight">
            {title}
          </h2>
          {subtitle ? (
            <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1">{subtitle}</div>
          ) : null}
        </div>
        <Button variant="ghost" size="icon-lg" onClick={onClose} aria-label={closeLabel}>
          <X aria-hidden />
        </Button>
      </header>
      <div className="px-5 py-4">{children}</div>
    </dialog>
  );
}
