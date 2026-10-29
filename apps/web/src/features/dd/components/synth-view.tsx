"use client";

/** Synth stage: run bar, the agent graph of one chapter, harmonize indicator, and the chapter list. The pipeline
 * page places the graph panel beside the planner's and the chapter list under both. */

import Link from "next/link";
import { useState } from "react";

import { type CrossStageBlocker, useCrossStageBlocker } from "../hooks/use-cross-stage-blocker";
import { type SynthRunController, useSynthRun } from "../hooks/use-synth-run";
import { chapterStudyHref } from "../params";
import { StageNodeBoard } from "./stage-node-board";
import { ChapterStrip, NoFrameworkCard, RunBar } from "./stage-parts";
import { SynthUsageSheet } from "./usage-sheet";

/** Says whose nodes the board shows: the nodes belong to one chapter's run, and with none picked every node
 * would read "pending" — which looks like nothing was written when the whole book is. */
function nodeBoardCaption(synth: SynthRunController): string {
  const picked = synth.cells.find((cell) => cell.selected);
  if (picked) return `How each node ran for “${picked.title}”`;
  if (synth.running) return "Nodes of the chapter being written";
  return "Pick a finished chapter in the list to see how each of its nodes ran.";
}

/** Run bar, volume-audit line and the node graph: two rows — the run bar, then everything under it — so the pipeline
 * page can lay it on shared grid rows (`className`). */
export function SynthRunPanel({
  synth,
  blocker,
  className = "",
}: {
  synth: SynthRunController;
  blocker: CrossStageBlocker;
  className?: string;
}) {
  const { harmonize } = synth;
  const [usageOpen, setUsageOpen] = useState(false);

  return (
    <div className={`flex min-w-0 flex-col gap-4 ${className}`}>
      <RunBar
        title="Synth"
        testId="synth"
        controls={synth}
        blockedMessage={blocker.message}
        onOpenUsage={() => setUsageOpen(true)}
      >
        {harmonize.status !== "idle" ? (
          <span data-testid="synth-harmonize" className="text-xs text-muted-foreground">
            Volume audit — {harmonize.label}
          </span>
        ) : null}
      </RunBar>
      <div className="flex min-w-0 flex-col gap-4">
        {synth.views.length > 0 ? (
          <section aria-label="Chapter pipeline">
            <StageNodeBoard
              stage="synth"
              views={synth.views}
              values={synth.values}
              liveLines={synth.liveLines}
              events={synth.events}
              threadId={synth.canvasThreadId}
              live={synth.running}
              caption={nodeBoardCaption(synth)}
            />
          </section>
        ) : null}
        {synth.error ? (
          <p role="alert" className="text-xs text-destructive-text">
            {synth.error}
          </p>
        ) : null}
      </div>
      {usageOpen ? (
        <SynthUsageSheet cells={synth.cells} live={synth.running} onClose={() => setUsageOpen(false)} />
      ) : null}
    </div>
  );
}

/** The chapter list — the per-chapter fan-out: run, wipe or read one chapter, or pick it to see its nodes. */
export function SynthChapters({
  slug,
  synth,
  blocker,
}: {
  slug: string;
  synth: SynthRunController;
  blocker: CrossStageBlocker;
}) {
  const studyHref = (chapterId: string) => chapterStudyHref(slug, chapterId);
  return (
    <div className="flex min-h-0 flex-col gap-2">
      <ChapterStrip
        cells={synth.cells}
        counter={synth.counter}
        runEnabled={!(synth.running || synth.starting || blocker.blocked)}
        runTitle={synth.running ? "A synthesis run is active — stop it first." : (blocker.message ?? "")}
        studyHref={studyHref}
        onRun={(id) => void synth.runChapter(id)}
        onWipe={(id) => void synth.wipeChapter(id)}
        onSelect={(id) => void synth.selectChapter(id)}
      />
      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <span>{synth.progress ? `Progress: ${synth.progress}` : ""}</span>
        {synth.viewChapterId ? (
          <Link href={studyHref(synth.viewChapterId)} className="hover:text-foreground">
            Open chapter in the study reader →
          </Link>
        ) : null}
      </div>
    </div>
  );
}

/** The synth screen without its data source, so the pipeline page can drive it from its own controller. */
function SynthBoard({
  slug,
  synth,
  blocker,
}: {
  slug: string;
  synth: SynthRunController;
  blocker: CrossStageBlocker;
}) {
  return (
    <div className="flex flex-col gap-4">
      <SynthRunPanel synth={synth} blocker={blocker} />
      <SynthChapters slug={slug} synth={synth} blocker={blocker} />
    </div>
  );
}

function SynthStage({ slug }: { slug: string }) {
  const blocker = useCrossStageBlocker("synth");
  const synth = useSynthRun(slug);
  return <SynthBoard slug={slug} synth={synth} blocker={blocker} />;
}

export function SynthView({ slug }: { slug: string | null }) {
  // `key`: a different framework is a fresh controller, so no state leaks between frameworks.
  return slug ? <SynthStage key={slug} slug={slug} /> : <NoFrameworkCard />;
}
