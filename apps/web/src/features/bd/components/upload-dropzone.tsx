"use client";

import { Upload } from "lucide-react";
import { type ChangeEvent, type DragEvent, useId, useState } from "react";
import { createPortal } from "react-dom";

import { describeUploadError } from "../api";
import { bookFileProblem } from "../domain/books";
import { useUploadBook } from "../hooks/use-bd-data";
import { useBdOverlays } from "../hooks/use-overlays";
import { MAX_UPLOAD_LABEL } from "../params";

/** PDF and EPUB files are checked here before anything is sent. The answer decides what happens next: a queued book (or one that
 * resumes) and a cached one are opened; one that is already being extracted is opened too, to follow its live run. */
export function UploadDropzone({
  onOpen,
  autoChain = false,
  progressHost,
}: {
  onOpen: (bookId: string) => void;
  autoChain?: boolean;
  progressHost: HTMLDivElement | null;
}) {
  const upload = useUploadBook();
  const { notice } = useBdOverlays();
  const inputId = useId();
  const hintId = useId();
  const errorId = useId();
  const [progress, setProgress] = useState<number | null>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const busy = upload.isPending;

  const submit = (file: File) => {
    const invalid = bookFileProblem(file);
    if (invalid) {
      setProblem(invalid);
      return;
    }
    setProblem(null);
    setFileName(file.name);
    setProgress(0);
    upload.mutate(
      { file, autoChain, onProgress: setProgress },
      {
        onSuccess: (result) => {
          if (result.status === "cached") notice("Already extracted — opened it.");
          else if (result.status === "locked")
            notice("This book is already being extracted — following the live run.");
          else notice(result.resumed ? "Resuming the extraction where it stopped." : "Extraction queued.");
          onOpen(result.book_id);
        },
        onError: (error) => setProblem(describeUploadError(error)),
        onSettled: () => {
          setProgress(null);
          setFileName(null);
        },
      },
    );
  };

  const onChoose = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = ""; // choosing the same file again must fire again
    if (file) submit(file);
  };

  const onDrop = (event: DragEvent<HTMLLabelElement>) => {
    event.preventDefault();
    setDragging(false);
    const file = event.dataTransfer.files[0];
    if (file && !busy) submit(file);
  };

  const percent = progress === null ? null : Math.round(progress * 100);

  return (
    <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
      <label
        htmlFor={inputId}
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={`inline-flex min-h-10 shrink-0 cursor-pointer items-center gap-2 rounded-sm border px-3 text-sm font-medium focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-ring ${
          dragging ? "border-primary bg-muted" : "border-border bg-background hover:border-neutral-8"
        } ${busy ? "cursor-progress opacity-80" : ""}`}
      >
        <Upload className="size-4 text-muted-foreground" aria-hidden="true" />
        <span>{busy ? "Uploading…" : dragging ? "Drop to upload" : "Upload book"}</span>
        <input
          id={inputId}
          type="file"
          accept="application/pdf,.pdf,application/epub+zip,.epub"
          className="sr-only"
          disabled={busy}
          aria-describedby={problem ? `${hintId} ${errorId}` : hintId}
          onChange={onChoose}
        />
      </label>
      <p id={hintId} className="text-xs text-muted-foreground">
        PDF or EPUB · up to {MAX_UPLOAD_LABEL}
      </p>
      {progressHost && percent !== null
        ? createPortal(
            <section
              role="status"
              aria-label="Book upload progress"
              aria-busy={busy}
              className="mb-3 rounded-md border border-border bg-card p-3"
            >
              <div className="flex min-w-0 flex-wrap items-center justify-between gap-x-4 gap-y-1">
                <p className="min-w-0 break-words text-sm font-medium text-foreground">
                  {percent >= 100 ? "Storing uploaded book" : "Uploading book"}
                  {fileName ? <span className="ml-2 font-normal text-muted-foreground">{fileName}</span> : null}
                </p>
                <span className="shrink-0 text-sm tabular text-muted-foreground">{percent}% sent</span>
              </div>
              <div
                role="progressbar"
                aria-label="Upload progress"
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={percent}
                className="mt-2 h-1.5 w-full overflow-hidden rounded-sm bg-muted"
              >
                <div className="h-full bg-primary transition-[width]" style={{ width: `${percent}%` }} />
              </div>
              <p aria-live="polite" className="mt-1.5 text-xs text-muted-foreground">
                {percent >= 100
                  ? "The file has been sent. The server is saving it and preparing the book record…"
                  : `Sending ${fileName ?? "your book"} to the server…`}
              </p>
            </section>,
            progressHost,
          )
        : null}
      {progressHost && problem
        ? createPortal(
            <p
              id={errorId}
              role="alert"
              className="mb-3 rounded-md border border-destructive bg-card p-3 text-sm text-destructive-text"
            >
              {problem}
            </p>,
            progressHost,
          )
        : !progressHost && problem
          ? (
              <p id={errorId} role="alert" className="w-full text-sm text-destructive-text">
                {problem}
              </p>
            )
          : null}
    </div>
  );
}
