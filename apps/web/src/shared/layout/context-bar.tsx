"use client";

import { type ReactNode, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";

import { CONTEXT_BAR_SLOT_ID } from "./context-bar-slot";

const subscribe = () => () => {};
const findHost = () => document.getElementById(CONTEXT_BAR_SLOT_ID) ?? document.body;
const noHost = () => null;

/** A page's own navbar, the third one: what the page is about on the left (`start`), its views on the right (`end`).
 * It is drawn into the app shell's slot under the section bar, so it spans the screen; the server render leaves it
 * out and the browser draws it on hydration. Not sticky: the two bars above stay, this one scrolls away. */
export function ContextBar({ start, end }: { start: ReactNode; end?: ReactNode }) {
  const host = useSyncExternalStore(subscribe, findHost, noHost);
  if (!host) return null;

  return createPortal(
    <div className="border-b border-border bg-background">
      <div className="mx-auto flex min-h-12 max-w-6xl flex-wrap items-center justify-between gap-x-6 gap-y-1 px-4 py-2 sm:px-6">
        {/* `relative`: on a phone a dropdown list spans this box, inside the bar's side padding. */}
        <div className="relative flex min-w-0 flex-wrap items-center gap-x-4 gap-y-1 max-sm:w-full">
          {start}
        </div>
        {end}
      </div>
    </div>,
    host,
  );
}
