import Link from "next/link";

import { BD_STAGES, type BdStage } from "../params";

/** The "Book Distiller" heading and the stage tabs as real links. Ingestion is the only stage so far; Distill and
 * Research are added to `BD_STAGES` and appear here. */
export function StageNav({ stage }: { stage: BdStage }) {
  return (
    <div className="mb-6">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">Book Distiller</h1>
        <p className="text-sm text-muted-foreground">
          Upload a book, watch it being read, study what was extracted
        </p>
      </div>
      <div className="mt-3 border-b border-border">
        <nav aria-label="Book Distiller stages" className="flex flex-wrap gap-x-1">
          {BD_STAGES.map((s) =>
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
                href={s.href}
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
