"use client";

/** Book Distiller's Agentic RAG pages. Ask reads URL state and therefore stays behind Suspense. */

import { Suspense } from "react";

import { BdOverlaysProvider } from "../../components/overlays";
import { AskStage } from "./ask-stage";
import { QueryStage } from "./query-stage";

const loading = <p className="text-sm text-muted-foreground">Loading…</p>;

export function BookAskPage() {
  return (
    <BdOverlaysProvider>
      <Suspense fallback={loading}>
        <AskStage />
      </Suspense>
    </BdOverlaysProvider>
  );
}

export function BookQueryPage() {
  return (
    <BdOverlaysProvider>
      <Suspense fallback={loading}>
        <QueryStage />
      </Suspense>
    </BdOverlaysProvider>
  );
}
