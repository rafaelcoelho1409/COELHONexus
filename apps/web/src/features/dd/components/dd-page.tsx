"use client";

/** Docs Distiller shell: the stage body. The title and stage tabs sit in the app's second navbar; the framework
 * picker and its facts in the third (`StageBar`). The stage comes from the pathname; slug/run come from search
 * params (read inside a Suspense boundary per Next.js requirements). */

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect } from "react";
import { DD_STAGES, pagesHref, type DdStage } from "../params";
import { AutoChainToggle } from "./auto-chain-toggle";
import { CatalogStage } from "./catalog-stage";
import { PagesStage } from "./pages-stage";
import { DdOverlaysProvider } from "./overlays";
import { PipelineView } from "./pipeline-view";
import { PlannerView } from "./planner-view";
import { StageBar } from "./stage-bar";
import { StudyView } from "./study-view";
import { SynthView } from "./synth-view";

const STAGE_BY_PATH = new Map<string, DdStage>(DD_STAGES.map((s) => [s.href, s.key]));
export function DocsDistillerPage() {
  const pathname = usePathname() ?? "/dd";
  const legacyIngestionUrl = pathname === "/dd/ingestion";
  const stage = legacyIngestionUrl ? "pages" : (STAGE_BY_PATH.get(pathname) ?? "catalog");
  return (
    <DdOverlaysProvider>
      <div className="mx-auto w-full max-w-screen-2xl p-4">
        {stage === "catalog" ? (
          <CatalogStage />
        ) : (
          <Suspense fallback={<p className="text-sm text-muted-foreground">Loading…</p>}>
            <StageWithSearchParams stage={stage} legacyIngestionUrl={legacyIngestionUrl} />
          </Suspense>
        )}
      </div>
    </DdOverlaysProvider>
  );
}

function StageWithSearchParams({
  stage,
  legacyIngestionUrl,
}: {
  stage: DdStage;
  legacyIngestionUrl: boolean;
}) {
  const params = useSearchParams();
  const router = useRouter();
  const slug = params.get("slug");
  const runId = params.get("run");
  useEffect(() => {
    if (legacyIngestionUrl) {
      router.replace(pagesHref(slug, runId), { scroll: false });
    }
  }, [legacyIngestionUrl, router, slug, runId]);

  const visibleStage = legacyIngestionUrl ? "pages" : stage;
  return (
    <>
      {/* Pages draws its own framework bar and controls. */}
      {visibleStage === "pages" ? null : (
        <StageBar
          stage={visibleStage}
          slug={slug}
          controls={visibleStage === "pipeline" ? <AutoChainToggle /> : undefined}
        />
      )}
      <StageBody stage={visibleStage} slug={slug} runId={runId} />
    </>
  );
}

function StageBody({ stage, slug, runId }: { stage: DdStage; slug: string | null; runId: string | null }) {
  if (stage === "pages") {
    return <PagesStage slug={slug} runId={runId} />;
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
