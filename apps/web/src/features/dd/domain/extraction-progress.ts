/** Text and percentage helpers shared by the Pages extraction progress UI. */

const PHASE_TEXT: Readonly<Record<string, string>> = {
  idle: "Waiting",
  waiting: "Waiting",
  queued: "Queued",
  running: "Processing",
  done: "Complete",
  failed: "Failed",
  cancelled: "Cancelled",
  aborted: "Stopped",
  downgrade: "Trying another tier",
  cancelling: "Stopping",
};

export function phaseStatusText(status: string | null | undefined, runStatus: string | null): string {
  const known = status ? PHASE_TEXT[status] : undefined;
  if (known) return known;
  if (runStatus === "failed") return "Skipped";
  if (runStatus === "cancelled") return "Stopped";
  return "Waiting";
}

export function extractionPercent(current: number, total: number): number | null {
  if (total <= 0) return null;
  return Math.max(0, Math.min(100, Math.round((current / total) * 100)));
}
