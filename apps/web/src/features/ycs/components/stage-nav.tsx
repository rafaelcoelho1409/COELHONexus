import Link from "next/link";

import { stageHref, YCS_STAGES, type YcsStage } from "../params";

/** The "YouTube Content Search" heading and the stage tabs as real links. Same shape as DD's `StageNav`;
 * YCS keeps no per-framework slug, so the tabs carry no query parameter. */
export function StageNav({ stage }: { stage: YcsStage }) {
  return (
    <div className="mb-6">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">YouTube Content Search</h1>
        <p className="text-sm text-muted-foreground">Find videos, ingest transcripts, ask, and query</p>
      </div>
      <div className="mt-3 flex flex-wrap items-end border-b border-border">
        <nav aria-label="YouTube Content Search stages" className="flex flex-wrap gap-x-1">
          {YCS_STAGES.filter((s) => s.tab || s.key === stage).map((s) =>
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
                href={stageHref(s.key)}
                className="-mb-px border-b-2 border-transparent px-3 py-2 text-sm text-muted-foreground hover:text-foreground"
              >
                {s.label}
              </Link>
            ),
          )}
        </nav>
      </div>
    </div>
  );
}
