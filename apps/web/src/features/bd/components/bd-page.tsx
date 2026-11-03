"use client";

/** Book Distiller shell: the stage strip and the stage body. The stage comes from the pathname, the open book from
 * the `?book=` search parameter (read inside a Suspense boundary, as Next.js requires). */

import { usePathname } from "next/navigation";
import { Suspense } from "react";

import { BD_STAGES, type BdStage } from "../params";
import { IngestionStage } from "./ingestion-stage";
import { BdOverlaysProvider } from "./overlays";
import { StageNav } from "./stage-nav";

const STAGE_BY_PATH = new Map<string, BdStage>(BD_STAGES.map((s) => [s.href, s.key]));

export function BookDistillerPage() {
  const pathname = usePathname() ?? "/bd";
  const stage = STAGE_BY_PATH.get(pathname) ?? "ingestion";
  return (
    <BdOverlaysProvider>
      <div className="mx-auto w-full max-w-6xl p-4">
        <StageNav stage={stage} />
        <Suspense fallback={<p className="text-sm text-muted-foreground">Loading…</p>}>
          <IngestionStage />
        </Suspense>
      </div>
    </BdOverlaysProvider>
  );
}
