"use client";

/** In-page notice (auto-hide) and a promise-based confirm dialog for the Book Distiller. The dialog is the native
 * `<dialog>`: focus is trapped, the page behind is inert, Escape cancels and focus returns to the control that
 * opened it. */

import { type ReactNode, useCallback, useEffect, useId, useMemo, useRef, useState } from "react";

import { useModalDialog } from "../hooks/use-modal-dialog";
import { type ConfirmOptions, OverlaysContext, type OverlaysValue } from "../hooks/use-overlays";
import { NOTICE_MS } from "../params";
import { ActionButton } from "./action-button";

type Notice = { message: string; tone: "info" | "error" };
type ConfirmRequest = { options: ConfirmOptions; resolve: (accepted: boolean) => void };

function ConfirmDialog({
  options,
  onClose,
}: {
  options: ConfirmOptions;
  onClose: (accepted: boolean) => void;
}) {
  const ref = useModalDialog();
  const titleId = useId();

  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      onCancel={(event) => {
        event.preventDefault(); // Escape: settle the promise through our state, once
        onClose(false);
      }}
      className="m-auto w-full max-w-md rounded-md border border-border bg-card p-4 text-foreground backdrop:bg-background/80"
    >
      <h2 id={titleId} className="text-base font-semibold">
        {options.title}
      </h2>
      <p className="mt-2 text-sm text-muted-foreground">{options.message}</p>
      <div className="mt-4 flex justify-end gap-2">
        <ActionButton onClick={() => onClose(false)}>Cancel</ActionButton>
        <ActionButton onClick={() => onClose(true)}>{options.confirmLabel}</ActionButton>
      </div>
    </dialog>
  );
}

export function BdOverlaysProvider({ children }: { children: ReactNode }) {
  const [notice, setNotice] = useState<Notice | null>(null);
  const [request, setRequest] = useState<ConfirmRequest | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const showNotice = useCallback((message: string, tone: Notice["tone"] = "info") => {
    setNotice({ message, tone });
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setNotice(null), NOTICE_MS);
  }, []);

  const confirm = useCallback(
    (options: ConfirmOptions) =>
      new Promise<boolean>((resolve) => {
        setRequest({ options, resolve });
      }),
    [],
  );

  const closeConfirm = (accepted: boolean) => {
    request?.resolve(accepted);
    setRequest(null);
  };

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );

  const value = useMemo<OverlaysValue>(() => ({ notice: showNotice, confirm }), [showNotice, confirm]);

  return (
    <OverlaysContext.Provider value={value}>
      {children}
      {/* An empty live region takes no height, so a hidden notice leaves no gap. */}
      <div aria-live="polite">
        {notice ? (
          <p
            className={`mt-4 rounded-md border border-border px-3 py-2 text-sm ${
              notice.tone === "error" ? "text-destructive-text" : "text-foreground"
            }`}
          >
            {notice.message}
          </p>
        ) : null}
      </div>
      {request ? <ConfirmDialog options={request.options} onClose={closeConfirm} /> : null}
    </OverlaysContext.Provider>
  );
}
