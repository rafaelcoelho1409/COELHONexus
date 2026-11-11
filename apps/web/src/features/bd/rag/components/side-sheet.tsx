"use client";

/** A right-hand sheet on the native modal `<dialog>`: focus is trapped, the page behind is inert, Escape or the close
 * button dismisses it, and focus returns to whatever opened it. Render it only while it is open. */

import { X } from "lucide-react";
import { type ReactNode, useId } from "react";
import { useModalDialog } from "../hooks/use-modal-dialog";
import { RagActionButton as Button } from "./rag-action-button";

export function SideSheet({
  title,
  subtitle,
  closeLabel,
  onClose,
  side = "right",
  children,
}: {
  title: string;
  subtitle?: ReactNode;
  closeLabel: string;
  onClose: () => void;
  side?: "left" | "right";
  children: ReactNode;
}) {
  const ref = useModalDialog();
  const titleId = useId();
  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      onCancel={(event) => {
        event.preventDefault(); // Escape: close through our state so the parent stays the one source of truth
        onClose();
      }}
      className={`m-0 h-dvh max-h-none w-full max-w-2xl overflow-y-auto border-border bg-background p-0 text-foreground backdrop:bg-background/80 ${
        side === "left" ? "mr-auto border-r" : "ml-auto border-l"
      }`}
    >
      <header className="sticky top-0 z-10 flex items-start gap-3 border-b border-border bg-background px-5 py-4">
        <div className="min-w-0 flex-1">
          <h2 id={titleId} className="text-lg font-semibold tracking-tight">
            {title}
          </h2>
          {subtitle ? <div className="mt-1 text-xs text-muted-foreground">{subtitle}</div> : null}
        </div>
        <Button variant="ghost" size="icon-lg" onClick={onClose} aria-label={closeLabel}>
          <X aria-hidden />
        </Button>
      </header>
      <div className="px-5 py-4">{children}</div>
    </dialog>
  );
}
