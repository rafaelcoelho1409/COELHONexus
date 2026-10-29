"use client";

/** Source · Videos — paste many video IDs/URLs (or upload / drop a .txt/.csv), see at once how many were understood, Fetch to
 * preview their metadata in the picker, then dispatch a picked subset — or everything pasted — through the videos pipeline.
 * The paste box remembers itself per browser and takes what the Search tab routes in. */

import { Trash2, Upload } from "lucide-react";
import type { DragEvent, FormEvent } from "react";
import { useRef, useState } from "react";

import { Button } from "@/shared/ui/button";
import { Label } from "@/shared/ui/label";

import { previewVideos, queueVideosPipeline } from "../api";
import { fmtCount, fmtInt, parseLangs, summarizePaste } from "../domain";
import { useBrowserPref } from "../hooks/use-browser-pref";
import { useDispatchPipeline } from "../hooks/use-dispatch-pipeline";
import { useYcsOverlays } from "../hooks/use-overlays";
import { pickerStatus, usePicker } from "../hooks/use-picker";
import { INGEST_ALL_CONFIRM_ABOVE, type SourceMode, VIDEOS_BUFFER_STORAGE_KEY } from "../params";
import { ModeHint } from "./mode-hint";
import { IngestBar, optionsSummary, StatusLine, TranscriptOptions } from "./options-bar";
import { PasteSummary } from "./paste-summary";
import { PickerView } from "./picker-view";

const NO_IDS_MESSAGE = "Paste at least one valid YouTube video ID or URL.";
const TEXT_FILE = /\.(txt|csv)$/i;

export function VideosTab({
  initialIds,
  onSwitch,
}: {
  initialIds: string[];
  /** Move what was pasted to another Source tab (the reader accepted the "looks like a playlist" suggestion). */
  onSwitch: (mode: Exclude<SourceMode, "search">, text: string) => void;
}) {
  const [buffer, setBuffer] = useBrowserPref(VIDEOS_BUFFER_STORAGE_KEY, "");
  // The routed batch is shown until the user edits the paste — then it becomes part of the buffer.
  const [consumed, setConsumed] = useState(initialIds.length === 0);
  const [status, setStatus] = useState<string | null>(null);
  const [include, setInclude] = useState(true);
  const [langs, setLangs] = useState("");
  const [fetched, setFetched] = useState(false);
  const [dragging, setDragging] = useState(false);
  const fileRef = useRef<HTMLInputElement | null>(null);

  const routedText = consumed ? "" : initialIds.join("\n");
  const value = buffer
    ? `${buffer}${routedText ? `${buffer.endsWith("\n") ? "" : "\n"}${routedText}` : ""}`
    : routedText;
  const summary = summarizePaste(value);
  const ids = summary.ids;

  const picker = usePicker((offset, limit) => previewVideos(ids, offset, limit));
  const dispatch = useDispatchPipeline();
  const overlays = useYcsOverlays();

  const onChange = (next: string) => {
    setConsumed(true);
    setBuffer(next);
  };

  const append = (text: string) => {
    setConsumed(true);
    setBuffer(value ? `${value}\n${text}` : text);
  };

  const readFile = async (file: File | undefined): Promise<void> => {
    if (!file) return;
    if (!TEXT_FILE.test(file.name)) {
      setStatus("Drop or choose a .txt or .csv file.");
      return;
    }
    setStatus(null);
    append(await file.text());
  };

  const onDrop = (event: DragEvent<HTMLTextAreaElement>): void => {
    event.preventDefault();
    setDragging(false);
    void readFile(event.dataTransfer?.files?.[0]);
  };

  const onFetch = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!ids.length) {
      setStatus(NO_IDS_MESSAGE);
      return;
    }
    setStatus(null);
    void picker
      .refetch()
      .then(() => setFetched(true))
      .catch(() => undefined);
  };

  const langsList = parseLangs(langs);

  const dispatchIds = async (videoIds: string[]): Promise<void> => {
    if (!videoIds.length) {
      setStatus(NO_IDS_MESSAGE);
      return;
    }
    const ok = await dispatch.dispatch(() =>
      queueVideosPipeline({
        video_ids: videoIds,
        include_transcription: include,
        transcription_languages: langsList,
      }),
    );
    if (ok) {
      setConsumed(true);
      setBuffer("");
    }
  };

  // Everything pasted is already known here, so Ingest all skips the picker — after asking, when it is a lot.
  const ingestAll = async (): Promise<void> => {
    if (ids.length > INGEST_ALL_CONFIRM_ABOVE) {
      const confirmed = await overlays.confirm({
        title: `Ingest all ${fmtInt(ids.length)} videos?`,
        message: `Queues every pasted video, not only the ones listed below. ${optionsSummary(include, langsList)}.`,
        confirmLabel: `Ingest ${fmtInt(ids.length)} videos`,
        tone: "primary",
      });
      if (!confirmed) return;
    }
    await dispatchIds(ids);
  };

  const ingestAllLabel = picker.total != null ? `Ingest all ${fmtCount(picker.total)}` : "Ingest all";
  const problem = dispatch.error ?? status ?? picker.error;

  return (
    <div className="flex flex-col gap-4">
      <form onSubmit={onFetch} className="flex flex-col gap-3 rounded-md border border-border bg-card p-3">
        <div>
          <div className="mb-1.5 flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
            <Label htmlFor="ycs-videos-input" className="text-xs font-medium text-muted-foreground">
              Video IDs or URLs
            </Label>
            <div className="flex items-center gap-1">
              <Button type="button" variant="ghost" size="lg" onClick={() => fileRef.current?.click()}>
                <Upload aria-hidden />
                Upload .txt or .csv
              </Button>
              <input
                ref={fileRef}
                type="file"
                accept=".txt,.csv,text/plain,text/csv"
                tabIndex={-1}
                aria-hidden
                className="sr-only"
                onChange={(event) => {
                  void readFile(event.currentTarget.files?.[0]);
                  event.currentTarget.value = ""; // so the same file can be chosen again
                }}
              />
              <Button
                type="button"
                variant="ghost"
                size="lg"
                disabled={value === ""}
                onClick={() => onChange("")}
              >
                <Trash2 aria-hidden />
                Clear
              </Button>
            </div>
          </div>
          <textarea
            id="ycs-videos-input"
            value={value}
            onChange={(event) => onChange(event.currentTarget.value)}
            onDrop={onDrop}
            onDragOver={(event) => {
              event.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            rows={6}
            placeholder={"dQw4w9WgXcQ\nhttps://www.youtube.com/watch?v=…\nhttps://youtu.be/…"}
            autoComplete="off"
            spellCheck={false}
            aria-describedby="ycs-videos-summary"
            aria-invalid={summary.invalid.length > 0}
            className={`w-full rounded-sm border bg-background px-2.5 py-2 font-mono text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 ${
              dragging ? "border-primary bg-primary/5" : "border-input"
            }`}
          />
          <div className="mt-1.5">
            <PasteSummary id="ycs-videos-summary" summary={summary} empty={value.trim() === ""} />
          </div>
        </div>

        <ModeHint text={value} own="videos" onSwitch={onSwitch} />

        <div className="flex flex-wrap items-end justify-between gap-x-4 gap-y-3">
          <TranscriptOptions
            idPrefix="ycs-videos"
            include={include}
            onInclude={setInclude}
            langs={langs}
            onLangs={setLangs}
          />
          <Button type="submit" size="lg" disabled={picker.loading}>
            {picker.loading ? "Fetching…" : "Fetch videos"}
          </Button>
        </div>
        <StatusLine text={problem ?? pickerStatus(picker)} error={problem !== null} />
      </form>

      {fetched ? (
        <>
          <PickerView picker={picker} fallbackTitle="Pasted videos" />
          <IngestBar
            selectedCount={picker.selected.size}
            ingestAllLabel={ingestAllLabel}
            summary={optionsSummary(include, langsList)}
            pending={dispatch.pending}
            canIngestAll={picker.items.length > 0}
            onStart={() => void dispatchIds(Array.from(picker.selected))}
            onIngestAll={() => void ingestAll()}
          />
        </>
      ) : null}
    </div>
  );
}
