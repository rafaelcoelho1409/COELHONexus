"use client";

/** Book Distiller shell: the stage body. The title and stage tabs sit in the app's second navbar. The open book comes
 * from the `?book=` search parameter (read inside a Suspense boundary, as Next.js requires). */

import { Suspense } from "react";

import { IngestionStage } from "./ingestion-stage";
import { BdOverlaysProvider } from "./overlays";

export function BookDistillerPage() {
  return (
    <BdOverlaysProvider>
      <div className="mx-auto w-full max-w-6xl p-4">
        <Suspense fallback={<p className="text-sm text-muted-foreground">Loading…</p>}>
          <IngestionStage />
        </Suspense>
      </div>
    </BdOverlaysProvider>
  );
}
