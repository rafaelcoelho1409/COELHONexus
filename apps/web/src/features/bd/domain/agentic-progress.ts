import type { AgenticProgress, ChapterRange } from "../entities";

export const AGENTIC_NODES = [
  { id: "audit_batch", label: "Audit pages" },
  { id: "plan_repairs", label: "Plan repairs" },
  { id: "repair_pages", label: "Repair pages" },
  { id: "normalize_metadata", label: "Normalize metadata" },
  { id: "publish", label: "Publish results" },
] as const;

export type AgenticNodeStatus = "pending" | "running" | "done" | "failed";

export const EMPTY_AGENTIC_PROGRESS: AgenticProgress = {
  current_node: null,
  node_status: {},
  audited_pages: [],
  audit_warning_pages: [],
  processed_pages: [],
  active_audit_pages: [],
  active_repair_pages: [],
};

export type ChapterProgress = ChapterRange & { processed: number; total: number };

export function chapterProgress(
  chapters: readonly ChapterRange[],
  processedPages: readonly number[],
): ChapterProgress[] {
  const processed = new Set(processedPages);
  return chapters.flatMap((chapter) => {
    const total = Math.max(0, chapter.last_page - chapter.first_page + 1);
    if (total === 0) return [];
    let count = 0;
    for (let page = chapter.first_page; page <= chapter.last_page; page += 1) {
      if (processed.has(page)) count += 1;
    }
    return [{ ...chapter, processed: count, total }];
  });
}

export function chapterProgressSummary(
  chapters: readonly ChapterRange[],
  processedPages: readonly number[],
): { completed: number; total: number } | null {
  const values = chapterProgress(chapters, processedPages);
  if (values.length === 0) return null;
  return {
    completed: values.filter((chapter) => chapter.processed === chapter.total).length,
    total: values.length,
  };
}

export function agenticNodeStatus(
  progress: AgenticProgress,
  nodeId: string,
  runStatus: string,
): AgenticNodeStatus {
  const status = progress.node_status[nodeId];
  if (status) return status;
  if (runStatus === "failed" && progress.current_node === nodeId) return "failed";
  return "pending";
}

export function agenticPercent(completed: number, total: number): number | null {
  if (total <= 0) return null;
  return Math.max(0, Math.min(100, Math.round((completed / total) * 100)));
}

export function agenticNodeCaption(
  progress: AgenticProgress,
  nodeId: string,
  total: number,
  unitPlural = "pages",
): string | null {
  if (total <= 0) return null;
  if (nodeId === "audit_batch") {
    const done = progress.audited_pages.length;
    const active = progress.active_audit_pages.length;
    return active > 0
      ? `${done} ${unitPlural} audited · ${active} active · ${total} total`
      : `${done} of ${total} ${unitPlural} audited`;
  }
  if (nodeId === "repair_pages") {
    const done = progress.processed_pages.length;
    const active = progress.active_repair_pages.length;
    return active > 0
      ? `${done} ${unitPlural} repaired · ${active} active · ${total} total`
      : `${done} of ${total} ${unitPlural} repaired`;
  }
  return null;
}
