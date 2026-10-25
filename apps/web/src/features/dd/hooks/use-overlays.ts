"use client";

import { createContext, useContext } from "react";

export type ConfirmOptions = { title: string; message: string; confirmLabel: string };

export type OverlaysValue = {
  /** Persistent, dismissible message. */
  toast: (message: string) => void;
  /** In-page message that hides itself. */
  notice: (message: string, tone?: "info" | "error") => void;
  /** Resolves true when the user confirms, false on cancel or Escape. */
  confirm: (options: ConfirmOptions) => Promise<boolean>;
};

export const OverlaysContext = createContext<OverlaysValue | null>(null);

export function useDdOverlays(): OverlaysValue {
  const value = useContext(OverlaysContext);
  if (!value) throw new Error("useDdOverlays must be used inside <DdOverlaysProvider>");
  return value;
}
