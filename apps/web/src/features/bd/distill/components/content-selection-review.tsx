"use client";

import Link from "next/link";
import { useState } from "react";
import type { ReactNode } from "react";

import { apiErrorMessage } from "@/shared/lib/api-error";

import { ActionButton } from "../../components/action-button";
import type { ContentSelection, ContentSelectionPage } from "../entities";
import { distillHref } from "../params";

const PAGE_SIZE = 20;
type SelectionFilter = "all" | "include" | "exclude";
type Disposition = "include" | "exclude";

export function ContentSelectionReview({
  bookId,
  agenticRunId,
  selection,
  pending,
  error,
  saving,
  saveError,
  onRetry,
  onSave,
}: {
  bookId: string;
  agenticRunId: string | null;
  selection: ContentSelection | null;
  pending: boolean;
  error: unknown;
  saving: boolean;
  saveError: unknown;
  onRetry: () => void;
  onSave: (expectedRevision: number, overrides: { page_number: number; disposition: Disposition }[]) => void;
}) {
  return (
    <section aria-labelledby="content-selection-heading" className="min-w-0 border-t border-border pt-4">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="mono-label">Distill input</p>
          <h3 id="content-selection-heading" className="mt-1 text-base font-semibold">
            Page selection
          </h3>
          <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
            Confident navigation, legal, blank, and noise pages are left out of the Learning Guide. The original
            Ingestion pages stay available, and uncertain pages stay included unless you change them here.
          </p>
        </div>
      </header>

      {agenticRunId === null ? (
        <p className="mt-3 rounded-sm border border-border px-3 py-2 text-sm text-muted-foreground">
          Complete Agentic ingestion to classify book pages for Distill.
        </p>
      ) : pending ? (
        <p role="status" className="mt-3 text-sm text-muted-foreground">
          Loading the page selection…
        </p>
      ) : error ? (
        <div role="alert" className="mt-3 rounded-sm border border-border p-3 text-sm">
          <p className="text-destructive-text">Page selection could not be loaded — {apiErrorMessage(error)}</p>
          <div className="mt-2">
            <ActionButton onClick={onRetry}>Try again</ActionButton>
          </div>
        </div>
      ) : selection ? (
        <SelectionEditor
          key={`${selection.revision}:${selection.sha256}`}
          bookId={bookId}
          selection={selection}
          saving={saving}
          saveError={saveError}
          onSave={onSave}
        />
      ) : (
        <p className="mt-3 rounded-sm border border-border px-3 py-2 text-sm text-muted-foreground">
          Page selection will be available when Agentic ingestion finishes.
        </p>
      )}
    </section>
  );
}

function SelectionEditor({
  bookId,
  selection,
  saving,
  saveError,
  onSave,
}: {
  bookId: string;
  selection: ContentSelection;
  saving: boolean;
  saveError: unknown;
  onSave: (expectedRevision: number, overrides: { page_number: number; disposition: Disposition }[]) => void;
}) {
  const [filter, setFilter] = useState<SelectionFilter>("all");
  const [pageIndex, setPageIndex] = useState(0);
  const [drafts, setDrafts] = useState<Record<number, Disposition>>({});
  const filtered = selection.pages.filter((page) => filter === "all" || page.disposition === filter);
  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const currentIndex = Math.min(pageIndex, pageCount - 1);
  const visiblePages = filtered.slice(currentIndex * PAGE_SIZE, (currentIndex + 1) * PAGE_SIZE);
  const changes = Object.entries(drafts).map(([pageNumber, disposition]) => ({
    page_number: Number(pageNumber),
    disposition,
  }));

  const updatePage = (page: ContentSelectionPage, disposition: Disposition) => {
    setDrafts((previous) => {
      const next = { ...previous };
      if (page.disposition === disposition) delete next[page.page_number];
      else next[page.page_number] = disposition;
      return next;
    });
  };

  const changeFilter = (next: SelectionFilter) => {
    setFilter(next);
    setPageIndex(0);
  };

  return (
    <div className="mt-3 min-w-0">
      <dl className="grid grid-cols-1 gap-2 sm:grid-cols-3">
        <SelectionMetric label="Included" count={selection.included_page_count} />
        <SelectionMetric label="Excluded" count={selection.excluded_page_count} />
        <SelectionMetric label="Book pages" count={selection.expected_page_count} />
      </dl>

      <div className="mt-3 flex flex-wrap items-center justify-between gap-3 border-y border-border py-3">
        <div>
          <p className="text-xs text-muted-foreground">
            Selection revision {selection.revision} · uncertain pages remain included by default
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            New runs pin this revision. Existing reports keep their original page selection.
          </p>
        </div>
        <ActionButton
          onClick={() => onSave(selection.revision, changes)}
          disabled={changes.length === 0 || saving}
          aria-busy={saving}
        >
          {saving ? "Saving…" : `Save ${changes.length} change${changes.length === 1 ? "" : "s"}`}
        </ActionButton>
      </div>

      {saveError ? (
        <p role="alert" className="mt-2 text-sm text-destructive-text">
          Changes could not be saved — {apiErrorMessage(saveError)}
        </p>
      ) : null}
      <p className="sr-only" role="status" aria-live="polite">
        {saving ? "Saving page selection." : changes.length > 0 ? `${changes.length} unsaved page changes.` : "Page selection saved."}
      </p>

      <div className="mt-3 flex flex-wrap items-center gap-2" role="group" aria-label="Filter selected pages">
        <FilterButton active={filter === "all"} onClick={() => changeFilter("all")}>
          All {selection.expected_page_count}
        </FilterButton>
        <FilterButton active={filter === "include"} onClick={() => changeFilter("include")}>
          Included {selection.included_page_count}
        </FilterButton>
        <FilterButton active={filter === "exclude"} onClick={() => changeFilter("exclude")}>
          Excluded {selection.excluded_page_count}
        </FilterButton>
      </div>

      {visiblePages.length === 0 ? (
        <p className="mt-3 rounded-sm border border-border p-3 text-sm text-muted-foreground">
          There are no pages in this selection.
        </p>
      ) : (
        <ol aria-label="Book page inclusion decisions" className="mt-3 divide-y divide-border border-y border-border">
          {visiblePages.map((page) => {
            const disposition = drafts[page.page_number] ?? page.disposition;
            const included = disposition === "include";
            return (
              <li key={page.page_number} className="flex min-w-0 flex-wrap items-start justify-between gap-3 py-3">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                    <Link
                      href={distillHref(bookId, null, page.page_number)}
                      className="font-mono text-sm font-medium underline-offset-2 hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                    >
                      Page {page.page_number}
                    </Link>
                    <span className="rounded-sm border border-border px-2 py-0.5 text-xs text-muted-foreground">
                      {page.role.replaceAll("_", " ")}
                    </span>
                    <span className="text-xs text-muted-foreground">
                      {Math.round(page.confidence * 100)}% confidence
                    </span>
                    <span className={included ? "text-xs text-success-text" : "text-xs text-warning-text"}>
                      {included ? "Included" : "Excluded"}
                    </span>
                  </div>
                  <p className="mt-1 break-words text-sm text-muted-foreground">{page.rationale}</p>
                </div>
                <button
                  type="button"
                  onClick={() => updatePage(page, included ? "exclude" : "include")}
                  aria-label={`${included ? "Exclude" : "Include"} page ${page.page_number} ${included ? "from" : "in"} Distill`}
                  aria-pressed={drafts[page.page_number] !== undefined}
                  className="min-h-9 shrink-0 rounded-sm border border-border px-3 text-sm hover:bg-muted focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                >
                  {included ? "Exclude" : "Include"}
                </button>
              </li>
            );
          })}
        </ol>
      )}

      {filtered.length > PAGE_SIZE ? (
        <nav aria-label="Page selection pages" className="mt-3 flex items-center justify-between gap-3">
          <p className="text-xs text-muted-foreground">
            {currentIndex * PAGE_SIZE + 1}–{Math.min((currentIndex + 1) * PAGE_SIZE, filtered.length)} of {filtered.length}
          </p>
          <div className="flex gap-2">
            <ActionButton onClick={() => setPageIndex((index) => Math.max(0, index - 1))} disabled={currentIndex === 0}>
              Previous
            </ActionButton>
            <ActionButton
              onClick={() => setPageIndex((index) => Math.min(pageCount - 1, index + 1))}
              disabled={currentIndex >= pageCount - 1}
            >
              Next
            </ActionButton>
          </div>
        </nav>
      ) : null}
    </div>
  );
}

function SelectionMetric({ label, count }: { label: string; count: number }) {
  return (
    <div className="border-l-2 border-border pl-3">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-1 text-lg font-semibold tabular-nums">{count.toLocaleString()}</dd>
    </div>
  );
}

function FilterButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`min-h-9 rounded-sm border px-3 text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring ${
        active ? "border-border bg-muted text-foreground" : "border-border text-muted-foreground hover:bg-muted"
      }`}
    >
      {children}
    </button>
  );
}
