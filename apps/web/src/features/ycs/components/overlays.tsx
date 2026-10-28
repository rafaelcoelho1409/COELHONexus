"use client";

/** In-page notice (auto-hide), toast (persistent, dismissible) and a promise-based confirm dialog,
 * scoped to YouTube Content Search. Same shape as the Docs Distiller's provider: the dialog is the
 * native `<dialog>` — focus is trapped, the page behind is inert, Escape cancels and focus returns to
 * the control that opened it. */

import { type ReactNode, useCallback, useEffect, useId, useMemo, useRef, useState } from "react";

import { useModalDialog } from "../hooks/use-modal-dialog";
import { type ConfirmOptions, OverlaysContext, type OverlaysValue } from "../hooks/use-overlays";
import { NOTICE_MS } from "../params";

type Toast = { id: number; message: string };
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
        event.preventDefault(); // Escape: resolve through our state so the promise settles once
        onClose(false);
      }}
      className="m-auto w-full max-w-md rounded-md border border-border bg-card p-4 text-foreground backdrop:bg-background/80"
    >
      <h2 id={titleId} className="text-base font-semibold">
        {options.title}
      </h2>
      <p className="mt-2 text-sm text-muted-foreground">{options.message}</p>
      <div className="mt-4 flex justify-end gap-2">
        <button
          type="button"
          onClick={() => onClose(false)}
          className="min-h-9 rounded-sm px-3 text-sm text-muted-foreground hover:text-foreground"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={() => onClose(true)}
          className="min-h-9 rounded-sm bg-destructive px-3 text-sm text-destructive-foreground"
        >
          {options.confirmLabel}
        </button>
      </div>
    </dialog>
  );
}

export function YcsOverlaysProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [request, setRequest] = useState<ConfirmRequest | null>(null);
  const nextId = useRef(1);
  const noticeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const showToast = useCallback((message: string) => {
    const id = nextId.current;
    nextId.current += 1;
    setToasts((list) => [...list, { id, message }]);
  }, []);

  const showNotice = useCallback((message: string, tone: Notice["tone"] = "info") => {
    setNotice({ message, tone });
    if (noticeTimer.current) clearTimeout(noticeTimer.current);
    noticeTimer.current = setTimeout(() => setNotice(null), NOTICE_MS);
  }, []);

  const confirm = useCallback(
    (options: ConfirmOptions) =>
      new Promise<boolean>((resolve) => {
        setRequest({ options, resolve });
      }),
    [],
  );

  const closeConfirm = useCallback(
    (accepted: boolean) => {
      request?.resolve(accepted);
      setRequest(null);
    },
    [request],
  );

  useEffect(
    () => () => {
      if (noticeTimer.current) clearTimeout(noticeTimer.current);
    },
    [],
  );

  const value = useMemo<OverlaysValue>(
    () => ({ toast: showToast, notice: showNotice, confirm }),
    [showToast, showNotice, confirm],
  );

  return (
    <OverlaysContext.Provider value={value}>
      {children}
      {notice ? (
        <p
          role="status"
          className={`mt-4 rounded-md border border-border px-3 py-2 text-sm ${
            notice.tone === "error" ? "text-destructive-text" : "text-foreground"
          }`}
        >
          {notice.message}
        </p>
      ) : null}
      <div className="fixed right-4 bottom-4 z-50 flex flex-col gap-2">
        {toasts.map((toast) => (
          <div
            key={toast.id}
            role="status"
            className="flex max-w-sm items-start gap-2 rounded-md border border-border bg-card px-3 py-2 text-sm text-foreground"
          >
            <span className="min-w-0 flex-1 break-words">{toast.message}</span>
            <button
              type="button"
              onClick={() => setToasts((list) => list.filter((item) => item.id !== toast.id))}
              aria-label="Dismiss notification"
              className="min-h-9 shrink-0 px-1 text-muted-foreground hover:text-foreground"
            >
              ×
            </button>
          </div>
        ))}
      </div>
      {request ? (
        <ConfirmDialog key={request.options.title} options={request.options} onClose={closeConfirm} />
      ) : null}
    </OverlaysContext.Provider>
  );
}
