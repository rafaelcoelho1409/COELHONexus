"use client";

import { type RefObject, useLayoutEffect, useRef } from "react";

/**
 * A ref for a native `<dialog>` that is opened modally as soon as it mounts and closed when it unmounts.
 * The browser then traps focus, makes the page behind inert, and hands focus back to the control that
 * opened it. Render the dialog only while it should be open.
 */
export function useModalDialog(): RefObject<HTMLDialogElement | null> {
  const ref = useRef<HTMLDialogElement>(null);

  useLayoutEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    // jsdom (unit tests) has no showModal/close; every browser does.
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.open = true;
    return () => {
      if (typeof dialog.close === "function") dialog.close();
    };
  }, []);

  return ref;
}
