"use client";

/** The third column of the Pipeline page, to the right of the planner's and synth's graphs (the original's chapter strip):
 * the chapters the planner proposes. While the planner is choosing them, its proposals as they appear; once a plan is
 * written, the plan's chapters with the synth state of each and the actions on one chapter (run, wipe, read). */

import type { CrossStageBlocker } from "../hooks/use-cross-stage-blocker";
import type { SynthRunController } from "../hooks/use-synth-run";
import { SynthChapters } from "./synth-view";

/** What the planner proposed, in the order it proposed it; read-only, since nothing can be run from a proposal. */
function ProposedChapters({ titles, planning }: { titles: readonly string[]; planning: boolean }) {
  return (
    <section
      aria-label="Proposed chapters"
      data-testid="proposed-chapters"
      className="rounded-md border border-border bg-card"
    >
      <div className="flex items-center justify-between border-b border-border px-3 py-2">
        <h3 className="text-xs font-medium text-muted-foreground">Proposed chapters</h3>
        <span className="font-mono text-xs text-muted-foreground">{titles.length}</span>
      </div>
      <ol>
        {titles.map((title, index) => (
          <li key={title} className="flex gap-2 border-b border-border px-3 py-2 text-sm">
            <span className="font-mono text-xs leading-5 text-muted-foreground">{index + 1}</span>
            <span className="min-w-0 break-words">{title}</span>
          </li>
        ))}
      </ol>
      <p className="px-3 py-2 text-xs text-muted-foreground">
        {planning
          ? "Proposed by the planner; the plan is still being written."
          : "Proposed by the planner; no plan has been written from them yet."}
      </p>
    </section>
  );
}

export function ChaptersColumn({
  slug,
  synth,
  blocker,
  proposed,
  planning,
}: {
  slug: string;
  synth: SynthRunController;
  blocker: CrossStageBlocker;
  /** Titles from the planner's proposal step (see `proposedTitles`). */
  proposed: readonly string[];
  /** The planner is running right now. */
  planning: boolean;
}) {
  // A re-plan shows its own proposals instead of the old plan's chapters; with no plan at all, proposals are all there is.
  const showProposals = proposed.length > 0 && (planning || synth.cells.length === 0);
  return showProposals ? (
    <ProposedChapters titles={proposed} planning={planning} />
  ) : (
    <SynthChapters slug={slug} synth={synth} blocker={blocker} />
  );
}
