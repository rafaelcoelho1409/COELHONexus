"use client";

/** What the Agentic AI step found on this page and what it did about it: issues with the text that shows them, what was
 * fixed automatically, what still needs a reviewer and the fix it would suggest. Shown only for a page the run processed.
 * A page that needs review opens it (on the page, not in the reading window); any other page keeps it to one line. */

import {
  describeMechanicalFixes,
  dispositionNote,
  issueCounts,
  issueLabel,
  RESOLUTION_LABELS,
} from "../domain/page-quality";
import { usePageQuality } from "../hooks/use-bd-data";

const RESOLUTION_STYLE = {
  auto_applied: "text-success-text",
  review: "text-warning-text",
  none: "text-muted-foreground",
} as const;

export function PageQualityPanel({
  bookId,
  page,
  unit,
  enabled,
  defaultOpen,
}: {
  bookId: string;
  page: number;
  unit: string;
  enabled: boolean;
  /** Open the report when the page needs review. */
  defaultOpen: boolean;
}) {
  const quality = usePageQuality(bookId, page, enabled);
  if (!enabled) return null;
  if (quality.isPending) {
    return (
      <p role="status" className="mt-3 text-xs text-muted-foreground">
        Loading the quality report…
      </p>
    );
  }
  if (quality.isError || !quality.data) return null;

  const report = quality.data;
  const counts = issueCounts(report);
  const note = dispositionNote(report);
  const fixes = describeMechanicalFixes(report.mechanical_fixes);
  const runNotes = [...report.audit_warnings, ...report.errors];

  return (
    <section aria-label={`${unit} quality`} className="mt-3 rounded-sm border border-border text-sm">
      <details key={page} open={defaultOpen && report.status === "review_required"} className="px-3">
        <summary className="flex min-h-10 cursor-pointer flex-wrap items-center gap-x-3 gap-y-1">
          <span className="font-medium">Quality</span>
          {report.status === "review_required" ? (
            <span className="text-xs font-medium text-warning-text">Needs review</span>
          ) : (
            <span className="text-xs font-medium text-success-text">Checked</span>
          )}
          <span className="text-xs tabular-nums text-muted-foreground">
            {report.issues.length === 0
              ? "No issues found"
              : `${report.issues.length} ${report.issues.length === 1 ? "issue" : "issues"} · ${counts.fixed} fixed automatically · ${counts.review} to review`}
          </span>
        </summary>
        <div className="pb-3">
          {note ? <p className="text-xs text-muted-foreground">{note}</p> : null}
          {fixes ? <p className="mt-2 text-xs text-muted-foreground">{fixes}</p> : null}
          {report.issues.length > 0 ? (
            <ul aria-label="Issues found" className="mt-3 flex flex-col gap-3">
              {report.issues.map((issue) => (
                <li key={issue.issue_id} className="min-w-0 border-l-2 border-border pl-3">
                  <p className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                    <span className="font-medium">{issueLabel(issue.code)}</span>
                    <span className={`text-xs font-medium ${RESOLUTION_STYLE[issue.resolution]}`}>
                      {RESOLUTION_LABELS[issue.resolution]}
                    </span>
                    {issue.severity ? (
                      <span className="text-xs text-muted-foreground">{issue.severity}</span>
                    ) : null}
                  </p>
                  {issue.description ? (
                    <p className="mt-1 text-xs text-muted-foreground">{issue.description}</p>
                  ) : null}
                  {issue.evidence.map((excerpt) => (
                    <pre
                      key={excerpt}
                      className="mt-1 whitespace-pre-wrap break-words rounded-sm bg-muted px-2 py-1 font-mono text-xs"
                    >
                      {excerpt}
                    </pre>
                  ))}
                  {issue.proposal && issue.resolution !== "auto_applied" ? (
                    <p className="mt-1 break-words text-xs">
                      <span className="text-muted-foreground">Suggested fix: </span>
                      <del className="font-mono text-destructive-text">{issue.proposal.target_text}</del>
                      <span aria-hidden="true"> → </span>
                      <span className="sr-only"> replaced by </span>
                      <ins className="font-mono text-success-text no-underline">
                        {issue.proposal.replacement}
                      </ins>
                    </p>
                  ) : null}
                </li>
              ))}
            </ul>
          ) : null}
          {runNotes.length > 0 ? (
            <details className="mt-3">
              <summary className="flex min-h-9 cursor-pointer items-center text-xs text-muted-foreground">
                Run notes · {runNotes.length}
              </summary>
              <ul className="mt-1 list-disc pl-5 text-xs text-muted-foreground">
                {runNotes.map((line) => (
                  <li key={line} className="break-words">
                    {line}
                  </li>
                ))}
              </ul>
            </details>
          ) : null}
        </div>
      </details>
    </section>
  );
}
