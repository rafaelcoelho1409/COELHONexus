"use client";

/** Docs Distiller shell: stage strip + framework picker + the stage body.
 * The stage comes from the pathname; slug/run come from search params
 * (read inside a Suspense boundary per Next.js requirements). */

import { usePathname, useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { DD_STAGES, type DdStage } from "../params";
import { CatalogStage } from "./catalog-stage";
import { FrameworkPicker } from "./framework-picker";
import { IngestionStage } from "./ingestion-stage";
import { DdOverlaysProvider } from "./overlays";
import { PipelineView } from "./pipeline-view";
import { PlannerView } from "./planner-view";
import { StageNav } from "./stage-nav";
import { StudyView } from "./study-view";
import { SynthView } from "./synth-view";

const STAGE_BY_PATH = new Map<string, DdStage>(DD_STAGES.map((s) => [s.href, s.key]));
export function DocsDistillerPage() {
  const pathname = usePathname() ?? "/dd";
  const stage = STAGE_BY_PATH.get(pathname) ?? "catalog";
  return (
    <DdOverlaysProvider>
      <div className="mx-auto w-full max-w-6xl p-4">
        {/* The tabs and picker need the slug from the URL, which Next only gives inside a Suspense boundary. */}
        <Suspense fallback={<StageNav stage={stage} slug={null} />}>
          <StageHeader stage={stage} />
        </Suspense>
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

function StageHeader({ stage }: { stage: DdStage }) {
  const slug = useSearchParams().get("slug");
  return (
    <StageNav
      stage={stage}
      slug={slug}
      aside={stage === "catalog" ? null : <FrameworkPicker stage={stage} currentSlug={slug} />}
    />
  );
}

function StageWithSearchParams({ stage }: { stage: DdStage }) {
  const params = useSearchParams();
  const slug = params.get("slug");
  const runId = params.get("run");
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
