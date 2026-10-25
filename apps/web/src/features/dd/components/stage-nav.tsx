import Link from "next/link";
import type { ReactNode } from "react";

import { DD_STAGES, type DdStage, stageHref } from "../params";

/** The "Docs Distiller" heading and the stage tabs as real links. A tab keeps the framework you are on
 * (`?slug=`), so moving from Planner to Synth does not ask you to pick it again. `aside` sits at the right end
 * of the tab row (the framework picker), which saves a row of height. */
export function StageNav({ stage, slug, aside }: { stage: DdStage; slug: string | null; aside?: ReactNode }) {
  return (
    <div className="mb-6">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">Docs Distiller</h1>
        <p className="text-sm text-muted-foreground">
          Ingest docs, plan distillation, study what was distilled
        </p>
      </div>
      <div className="mt-3 flex flex-wrap items-end justify-between gap-x-4 gap-y-2 border-b border-border">
        <nav aria-label="Docs Distiller stages" className="flex flex-wrap gap-x-1">
          {DD_STAGES.map((s) =>
            s.key === stage ? (
              <span
                key={s.key}
                aria-current="page"
                className="-mb-px border-b-2 border-primary px-3 py-2 text-sm font-medium text-primary-text"
              >
                {s.label}
              </span>
            ) : (
              <Link
                key={s.key}
                href={stageHref(s.key, slug)}
                className="-mb-px border-b-2 border-transparent px-3 py-2 text-sm text-muted-foreground hover:text-foreground"
              >
                {s.label}
              </Link>
            ),
          )}
        </nav>
        {aside ? <div className="flex flex-wrap items-center gap-x-4 gap-y-2 pb-2">{aside}</div> : null}
      </div>
    </div>
  );
}
