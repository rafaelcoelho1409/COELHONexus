"use client";

import dynamic from "next/dynamic";

import type { QueryEditorProps } from "./query-editor-cm";

// The editor library is only needed once the Query stage is on screen. The placeholder holds the editor's
// minimum height, so nothing below it jumps when the editor arrives.
const Editor = dynamic(() => import("./query-editor-cm").then((module) => module.QueryEditorCm), {
  ssr: false,
  loading: () => (
    <p className="flex min-h-36 items-center justify-center rounded-sm border border-border text-xs text-muted-foreground">
      Loading editor…
    </p>
  ),
});

/** The code editor for a query, loaded on demand. */
export function QueryEditor(props: QueryEditorProps) {
  return <Editor {...props} />;
}
