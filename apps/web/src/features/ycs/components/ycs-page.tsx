"use client";

/** The YouTube Content Search page: the stage body (the stage tabs sit in the app's second navbar). The Ingestion
 * and Ask stages read search params (inside a Suspense boundary per Next.js requirements); Source and Query keep
 * their state locally. Route pages re-export this single component. */

import { usePathname } from "next/navigation";
import type { JSX } from "react";
import { Suspense } from "react";
import { stageByHref, type YcsStage } from "../params";
import { AskStage } from "./ask-stage";
import { IngestionStage } from "./ingestion-stage";
import { YcsOverlaysProvider } from "./overlays";
import { QueryStage } from "./query-stage";
import { SourceStage } from "./source-stage";

export function YcsPage(): JSX.Element {
  const pathname = usePathname();
  const stage = stageByHref(pathname ?? "/ycs");
  return (
    <YcsOverlaysProvider>
      <div>
        {stage === "ingestion" || stage === "ask" || stage === "query" ? (
          <Suspense fallback={<p className="text-sm text-muted-foreground">Loading…</p>}>
            <StageBody stage={stage} />
          </Suspense>
        ) : (
          <SourceStage />
        )}
      </div>
    </YcsOverlaysProvider>
  );
}

function StageBody({ stage }: { stage: Exclude<YcsStage, "source"> }): JSX.Element {
  if (stage === "ingestion") return <IngestionStage />;
  if (stage === "ask") return <AskStage />;
  return <QueryStage />;
}
