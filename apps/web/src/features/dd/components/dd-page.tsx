"use client";

/** Docs Distiller shell: the stage body. The title and stage tabs sit in the app's second navbar; the framework
 * picker and its facts in the third (`StageBar`). The stage comes from the pathname; slug/run come from search
 * params (read inside a Suspense boundary per Next.js requirements). */

import { usePathname, useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { DD_STAGES, type DdStage } from "../params";
import { AutoChainToggle } from "./auto-chain-toggle";
import { CatalogStage } from "./catalog-stage";
import { IngestionStage } from "./ingestion-stage";
import { DdOverlaysProvider } from "./overlays";
import { PipelineView } from "./pipeline-view";
import { PlannerView } from "./planner-view";
import { StageBar } from "./stage-bar";
import { StudyView } from "./study-view";
import { SynthView } from "./synth-view";

const STAGE_BY_PATH = new Map<string, DdStage>(DD_STAGES.map((s) => [s.href, s.key]));
export function DocsDistillerPage() {
  const pathname = usePathname() ?? "/dd";
  const stage = STAGE_BY_PATH.get(pathname) ?? "catalog";
  return (
    <DdOverlaysProvider>
      <div className="mx-auto w-full max-w-6xl p-4">
        {stage === "catalog" ? (
          <CatalogStage />
        ) : (
          <Suspense fallback={<p className="text-sm text-muted-foreground">Loading…</p>}>
            <StageWithSearchParams stage={stage} />
          </Suspense>
        )}
      </div>
    </DdOverlaysProvider>
  );
}

function StageWithSearchParams({ stage }: { stage: DdStage }) {
  const params = useSearchParams();
  const slug = params.get("slug");
  const runId = params.get("run");
  return (
    <>
      {/* Ingestion draws its own bar: its Agents | Pages views sit on the right of it. */}
      {stage === "ingestion" ? null : (
        <StageBar
          stage={stage}
          slug={slug}
          controls={stage === "pipeline" ? <AutoChainToggle /> : undefined}
        />
      )}
      <StageBody stage={stage} slug={slug} runId={runId} />
    </>
  );
}

function StageBody({ stage, slug, runId }: { stage: DdStage; slug: string | null; runId: string | null }) {
  if (stage === "ingestion") {
    return <IngestionStage slug={slug} runId={runId} />;
  }
  if (stage === "pipeline") {
    return <PipelineView slug={slug} />;
  }
  if (stage === "planner") {
    return <PlannerView slug={slug} />;
  }
  if (stage === "synth") {
    return <SynthView slug={slug} />;
  }
  return <StudyView slug={slug} />;
}
