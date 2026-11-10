"use client";

/** Synth stage: run bar, the agent graph of one chapter, harmonize indicator, and the chapter list. The Pipeline tab
 * uses separate progress and graph panels above its chapter column. */

import Link from "next/link";
import { useState } from "react";

import { type CrossStageBlocker, useCrossStageBlocker } from "../hooks/use-cross-stage-blocker";
import { type SynthRunController, useSynthRun } from "../hooks/use-synth-run";
import { chapterStudyHref } from "../params";
import { StageNodeBoard } from "./stage-node-board";
import { ChapterStrip, NoFrameworkCard, PipelineStageProgress, RunBar } from "./stage-parts";
import { SynthUsageSheet } from "./usage-sheet";

/** Says whose nodes the board shows: the nodes belong to one chapter's run, and with none picked every node
 * would read "pending" — which looks like nothing was written when the whole book is. */
function nodeBoardCaption(synth: SynthRunController): string {
  if (synth.views.length === 0) return "Compiled Synth topology · run one chapter to see execution status.";
  const picked = synth.cells.find((cell) => cell.selected);
  if (picked) return `How each node ran for “${picked.title}”`;
  if (synth.running) return "Nodes of the chapter being written";
  return "Pick a finished chapter in the list to see how each of its nodes ran.";
}

/** The standalone Synth view keeps the run bar and node graph in one card; the Pipeline tab uses separate panels. */
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
  const noEligibleChapters =
    synth.cells.length > 0 && synth.cells.every((cell) => cell.synthEligible === false);

  return (
    <>
      <section
        aria-label="Synth Agentic AI"
        className={`flex min-w-0 flex-col gap-4 rounded-md border border-border bg-card p-3 ${className}`}
      >
        <RunBar
          title="Synth"
          testId="synth"
          controls={synth}
          blockedMessage={
            blocker.message ?? (noEligibleChapters ? "This plan has no chapters eligible for Synth." : null)
          }
          onOpenUsage={() => setUsageOpen(true)}
          embedded
        >
          {harmonize.status !== "idle" ? (
            <span data-testid="synth-harmonize" className="text-xs text-muted-foreground">
              Volume audit — {harmonize.label}
            </span>
          ) : null}
        </RunBar>
        <div className="flex min-w-0 flex-col gap-4">
          <StageNodeBoard
            stage="synth"
            views={synth.views}
            values={synth.values}
            liveLines={synth.liveLines}
            events={synth.events}
            threadId={synth.canvasThreadId}
            live={synth.running}
            embedded
            caption={nodeBoardCaption(synth)}
          />
          {synth.error ? (
            <p role="alert" className="text-xs text-destructive-text">
              {synth.error}
            </p>
          ) : null}
        </div>
      </section>
      {usageOpen ? (
        <SynthUsageSheet cells={synth.cells} live={synth.running} onClose={() => setUsageOpen(false)} />
      ) : null}
    </>
  );
}

/** Pipeline's first-row progress card, measured in eligible chapter outputs. */
export function SynthProgressPanel({
  synth,
  blocker,
}: {
  synth: SynthRunController;
  blocker: CrossStageBlocker;
}) {
  const { harmonize } = synth;
  const [usageOpen, setUsageOpen] = useState(false);
  const eligible = synth.cells.filter((cell) => cell.synthEligible !== false);
  const processed = eligible.filter((cell) =>
    ["done", "needs_review", "failed", "cancelled"].includes(cell.status),
  ).length;
  const running = eligible.filter((cell) => cell.status === "running").length;
  const excluded = synth.cells.length - eligible.length;
  const total = eligible.length;
  const current = Math.min(total, processed + running);
  const summary = total
    ? `${synth.counter}${running ? ` · ${running} running` : ""}`
    : excluded
      ? `No eligible chapters · ${excluded} excluded from Synth`
      : "No planned chapters yet.";
  const valueText = total
    ? `${current} of ${total} Synth chapters processed${running ? `, ${running} running` : ""}`
    : "No eligible Synth chapters";
  const noEligibleChapters = synth.cells.length > 0 && eligible.length === 0;

  return (
    <>
      <PipelineStageProgress
        title="Synth"
        testId="synth"
        controls={synth}
        blockedMessage={
          blocker.message ?? (noEligibleChapters ? "This plan has no chapters eligible for Synth." : null)
        }
        error={synth.error}
        onOpenUsage={() => setUsageOpen(true)}
        current={current}
        total={total}
        valueText={valueText}
        summary={summary}
        indeterminate={synth.running}
      >
        {harmonize.status !== "idle" ? (
          <span data-testid="synth-harmonize" className="text-xs text-muted-foreground">
            Volume audit — {harmonize.label}
          </span>
        ) : null}
      </PipelineStageProgress>
      {usageOpen ? (
        <SynthUsageSheet cells={synth.cells} live={synth.running} onClose={() => setUsageOpen(false)} />
      ) : null}
    </>
  );
}

/** Synth's lower-row node graph box for the shared Pipeline layout. */
export function SynthGraphPanel({ synth }: { synth: SynthRunController }) {
  return (
    <section
      aria-label="Synth Agentic AI graph"
      className="flex min-w-0 flex-col gap-3 rounded-md border border-border bg-card p-3"
    >
      <h3 className="text-sm font-medium">Synth graph</h3>
      <StageNodeBoard
        stage="synth"
        views={synth.views}
        values={synth.values}
        liveLines={synth.liveLines}
        events={synth.events}
        threadId={synth.canvasThreadId}
        live={synth.running}
        embedded
        caption={nodeBoardCaption(synth)}
      />
    </section>
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
  const noEligibleChapters =
    synth.cells.length > 0 && synth.cells.every((cell) => cell.synthEligible === false);
  return (
    <div className="flex min-h-0 flex-col gap-2">
      <ChapterStrip
        cells={synth.cells}
        counter={synth.counter}
        runEnabled={!(synth.running || synth.starting || blocker.blocked || noEligibleChapters)}
        runTitle={
          noEligibleChapters
            ? "This plan has no chapters eligible for Synth."
            : synth.running
              ? "A synthesis run is active — stop it first."
              : (blocker.message ?? "")
        }
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
